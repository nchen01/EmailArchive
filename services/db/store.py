"""Persistence entry points (spec 04 §1 idempotent writes).

``persist_l0`` writes threads + messages (+ attachments); ``persist_l1`` writes
orgs, persons, identities, edges. Both use PostgreSQL ``INSERT ... ON CONFLICT
DO UPDATE`` so re-running is a no-op on row count (schema convention #5).

Dedupe keys (spec 04 §1):
- message  → ``(mailbox_id, message_id_header)``
- thread / org / person → ``id``
- identity → ``(mailbox_id, email)``
- edge → ``(mailbox_id, person_id)``
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from services.enrich.pipeline import EnrichResult
from services.ingest.store import IngestStore

from . import mappers
from . import models as orm


# ── Audit log (append-only, spec 00 §12/§16) ─────────────────────────────────

def write_audit_event(
    session: Session,
    *,
    mailbox_id: str,
    actor: str,
    action: str,
    scope: str | None = None,
    message_count: int | None = None,
    sync_token: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    """Append one immutable row to audit_log (never UPDATE, spec 00 §12).

    Call twice per ingest run:
      1. action="ingest_start" before any data is read (started_at set, rest None).
      2. action="ingest_finish" after completion (finished_at, message_count, sync_token set).

    Two rows preserves the append-only contract; a single-row update would not.
    ``actor`` should be the OAuth subject claim, never a raw token.
    """
    now = datetime.now(timezone.utc)
    session.execute(
        insert(orm.AuditLog).values(
            mailbox_id=mailbox_id,
            actor=actor,
            action=action,
            scope=scope,
            message_count=message_count,
            started_at=started_at or now,
            finished_at=finished_at,
            sync_token=sync_token,
        )
    )
    session.commit()


def save_sync_token(session: Session, mailbox_id: str, sync_token: str) -> None:
    """Upsert the Gmail historyId / Graph delta token for incremental ingest."""
    session.execute(
        insert(orm.SyncState)
        .values(mailbox_id=mailbox_id, sync_token=sync_token, last_run_at=datetime.now(timezone.utc))
        .on_conflict_do_update(
            index_elements=["mailbox_id"],
            set_={"sync_token": sync_token, "last_run_at": datetime.now(timezone.utc)},
        )
    )
    session.commit()


def load_sync_token(session: Session, mailbox_id: str) -> str | None:
    """Return the stored sync token for this mailbox, or None if never run."""
    row = session.get(orm.SyncState, mailbox_id)
    return row.sync_token if row else None


def clear_mailbox_snapshot_for_reingest(
    session: Session, mailbox_id: str, *, commit: bool = True
) -> dict[str, int]:
    """Delete ALL mailbox-scoped derived data for a clean re-ingest snapshot (S16.0).

    Removes every L0/L1/L2 row belonging to THIS mailbox, in FK-safe order
    (children before parents), so a subsequent date-windowed ingest leaves the
    product surfaces reflecting only the newly-ingested window.

    Hard boundaries (destructive — call ONLY after an explicit, confirmed
    replace request):
      - scoped strictly by ``mailbox_id`` (directly, or via a subquery over this
        mailbox's own messages/projects) — never touches another mailbox;
      - NEVER deletes ``audit_log`` (append-only retention, spec 00 §12);
      - NEVER deletes the ``mailbox`` row itself;
      - clears ``sync_state`` too, so the wiped mailbox carries no stale
        incremental token (a windowed snapshot never saves one);
      - preserves operator config (``project_label_override``) — it is not
        ingest-derived state.

    Cascade is not assumed: ``identity`` has no FK to ``mailbox``, and the
    composite-key child tables are cleared explicitly. Returns per-table counts.

    ``commit=False`` leaves the deletes pending in the caller's transaction — used
    by replace-mode ingest so the clear and the subsequent L0 write commit
    together (a failed L0 persist rolls the clear back too).
    """
    from sqlalchemy import delete, select

    mid = mailbox_id
    msg_ids = select(orm.Message.id).where(orm.Message.mailbox_id == mid)
    proj_ids = select(orm.Project.id).where(orm.Project.mailbox_id == mid)

    counts: dict[str, int] = {}

    def _del(stmt, name: str) -> None:
        res = session.execute(stmt.execution_options(synchronize_session=False))
        counts[name] = int(res.rowcount or 0)

    # ── L2 embeddings + L0 attachments (message children) ────────────────────
    _del(delete(orm.MessageEmbedding).where(orm.MessageEmbedding.mailbox_id == mid), "message_embedding")
    _del(delete(orm.MessageAttachment).where(orm.MessageAttachment.message_id.in_(msg_ids)), "message_attachment")
    # ── L1 events + project graph (project/person children first) ────────────
    _del(delete(orm.Event).where(orm.Event.mailbox_id == mid), "event")
    _del(delete(orm.ThreadProjectAssignment).where(orm.ThreadProjectAssignment.project_id.in_(proj_ids)), "thread_project_assignment")
    _del(delete(orm.ProjectMember).where(orm.ProjectMember.project_id.in_(proj_ids)), "project_member")
    _del(delete(orm.Project).where(orm.Project.mailbox_id == mid), "project")
    _del(delete(orm.Edge).where(orm.Edge.mailbox_id == mid), "edge")
    # identity.mailbox_id is TEXT with no FK cascade — must be explicit.
    _del(delete(orm.Identity).where(orm.Identity.mailbox_id == mid), "identity")
    _del(delete(orm.Person).where(orm.Person.mailbox_id == mid), "person")
    _del(delete(orm.Org).where(orm.Org.mailbox_id == mid), "org")
    # ── L0 messages + threads (parents last) ─────────────────────────────────
    _del(delete(orm.Message).where(orm.Message.mailbox_id == mid), "message")
    _del(delete(orm.Thread).where(orm.Thread.mailbox_id == mid), "thread")
    # ── Operational: drop the stale incremental token (scoped snapshot) ──────
    _del(delete(orm.SyncState).where(orm.SyncState.mailbox_id == mid), "sync_state")

    if commit:
        session.commit()
    return counts


def _upsert(session: Session, model, rows: list[dict], index_elements: list[str]) -> None:
    """Upsert ``rows`` into ``model``, updating all non-key columns on conflict."""
    if not rows:
        return
    stmt = insert(model).values(rows)
    pk_cols = {c.name for c in model.__table__.primary_key.columns}
    # Never UPDATE primary-key, conflict-target, or created_at columns.
    skip = pk_cols | set(index_elements) | {"created_at"}
    update_cols = {
        c.name: stmt.excluded[c.name]
        for c in model.__table__.columns
        if c.name not in skip
    }
    if update_cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements, set_=update_cols
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=index_elements)
    session.execute(stmt)


def persist_l0(
    store: IngestStore,
    mailbox_id: str,
    session: Session,
    *,
    replace_snapshot: bool = False,
) -> None:
    """Upsert threads then messages (+ attachments). Idempotent on message_id_header.

    Default (``replace_snapshot=False``): pure upsert — adds or updates rows in
    *store* and never deletes rows that are absent from the batch. This is the
    correct semantics for incremental/partial sync (spec 00 §5, §13): a
    historyId/delta run only contains changed messages, not the full mailbox.

    ``replace_snapshot=True``: before upserting, delete any existing
    thread/message rows for this mailbox whose PK does not appear in *store*.
    Use this only for:
      - Full re-ingest / dev seed (all messages present in the store).
      - Legacy-ID upgrade (unscoped → scoped db_mailbox_id): the upsert cannot
        change PKs, so stale rows must be removed first or attachment FKs break.
    The thread→message→message_attachment CASCADE chain handles child cleanup.
    """
    from sqlalchemy import delete, select

    if replace_snapshot:
        # ── Stale thread removal ─────────────────────────────────────────────
        new_thread_ids = {t.id for t in store.threads}
        stale_thr_ids = [
            row
            for (row,) in session.execute(
                select(orm.Thread.id).where(orm.Thread.mailbox_id == mailbox_id)
            ).all()
            if row not in new_thread_ids
        ]
        if stale_thr_ids:
            # CASCADE: thread → message → message_attachment
            session.execute(delete(orm.Thread).where(orm.Thread.id.in_(stale_thr_ids)))

        # ── Stale message removal (belt-and-suspenders for edge cases) ───────
        new_msg_ids = {m.id for m in store.messages}
        stale_msg_ids = [
            row
            for (row,) in session.execute(
                select(orm.Message.id).where(orm.Message.mailbox_id == mailbox_id)
            ).all()
            if row not in new_msg_ids
        ]
        if stale_msg_ids:
            # CASCADE: message → message_attachment
            session.execute(delete(orm.Message).where(orm.Message.id.in_(stale_msg_ids)))

    # ── Upserts ───────────────────────────────────────────────────────────────
    thread_rows = [mappers.thread_to_row(t, mailbox_id) for t in store.threads]
    _upsert(session, orm.Thread, thread_rows, ["id"])

    message_rows = [mappers.message_to_row(m, mailbox_id) for m in store.messages]
    _upsert(
        session,
        orm.Message,
        message_rows,
        ["mailbox_id", "message_id_header"],
    )

    # Attachments: child rows keyed (message_id, sha256). Re-deriving the message
    # id is deterministic, so an upsert on the composite key is idempotent.
    attach_rows: list[dict] = []
    for m in store.messages:
        attach_rows.extend(mappers.attachment_rows(m))
    _upsert(session, orm.MessageAttachment, attach_rows, ["message_id", "sha256"])

    session.commit()


def persist_l1(result: EnrichResult, mailbox_id: str, session: Session) -> None:
    """Upsert orgs, persons, identities, edges (+ clustering if present).

    Idempotent on id / natural keys. Clustering uses a selective upsert:
    only projects absent from the new result are deleted (their membership and
    assignment children cascade); existing stable project rows are updated in
    place so downstream FKs (e.g. future event.project_id) are never broken.
    """
    org_rows = [mappers.org_to_row(o, mailbox_id) for o in result.orgs]
    _upsert(session, orm.Org, org_rows, ["id"])

    person_rows = [mappers.person_to_row(p, mailbox_id) for p in result.people]
    _upsert(session, orm.Person, person_rows, ["id"])

    identity_rows = [mappers.identity_to_row(i, mailbox_id) for i in result.identities]
    _upsert(session, orm.Identity, identity_rows, ["mailbox_id", "email"])

    edge_rows = [mappers.edge_to_row(e, mailbox_id) for e in result.edges]
    _upsert(session, orm.Edge, edge_rows, ["mailbox_id", "person_id"])

    if result.clustering is not None:
        _persist_clustering(result.clustering, mailbox_id, session)

    # Always call persist_events when extraction ran (even if no events were
    # produced): processed_headers is non-empty iff extract_fn was supplied,
    # and we must delete stale Events for those threads regardless.
    if result.event_processed_headers:
        persist_events(
            result.events, mailbox_id, session,
            processed_headers=result.event_processed_headers,
        )

    session.commit()


def persist_events(
    events: list,
    mailbox_id: str,
    session: Session,
    *,
    processed_headers: list[str],
) -> None:
    """Persist Events via scoped delete-and-reinsert (S4 guide §4, D10).

    The delete scope is ``processed_headers`` — ALL message_id_header values
    from threads that extraction ran on (including threads that produced zero
    Events). This ensures stale Events are removed even when re-extraction
    yields nothing or cites different messages.

    The ``ix_event_srcs`` GIN index covers the ``&&`` overlap efficiently.
    Safe for incremental sync: only clears Events whose source messages are
    in the processed batch; untouched threads' Events are left intact.
    """
    from sqlalchemy import delete

    if processed_headers:
        session.execute(
            delete(orm.Event).where(
                orm.Event.mailbox_id == mailbox_id,
                orm.Event.source_message_ids.overlap(processed_headers),  # SQL: &&
            )
        )

    if events:
        rows = [mappers.event_to_row(e, mailbox_id) for e in events]
        # IDs are fresh uuid4 per run, so a plain insert is correct after the delete.
        _upsert(session, orm.Event, rows, ["id"])
    session.commit()


def _persist_clustering(clustering, mailbox_id: str, session: Session) -> None:
    """Upsert the clustering result; only delete projects that disappeared.

    Project IDs are stable across re-clusters (uuid5 + carry_over_ids), so we
    must not delete-all-then-reinsert: that would break any future FK that points
    at project.id (e.g. event.project_id with ON DELETE SET NULL).  Instead:

    1. Delete only projects absent from the new result (cascade removes their
       memberships and assignments automatically via ON DELETE CASCADE).
    2. Upsert all current projects (stable IDs survive unchanged).
    3. Delete + reinsert memberships and assignments for current projects, because
       involvement weights may have changed even when the project ID is the same.
    """
    from sqlalchemy import delete, select

    new_project_ids = {p.id for p in clustering.projects}

    existing_ids = set(
        session.execute(
            select(orm.Project.id).where(orm.Project.mailbox_id == mailbox_id)
        ).scalars()
    )
    removed_ids = existing_ids - new_project_ids
    if removed_ids:
        # ON DELETE CASCADE handles ThreadProjectAssignment + ProjectMember rows.
        session.execute(delete(orm.Project).where(orm.Project.id.in_(removed_ids)))

    # Upsert projects — insert new ones, update label/confidence/span on existing.
    project_rows = [mappers.project_to_row(p, mailbox_id) for p in clustering.projects]
    _upsert(session, orm.Project, project_rows, ["id"])

    # Refresh memberships and assignments for all current projects.
    # Delete-then-reinsert is safe here: these child rows carry no downstream FKs.
    if new_project_ids:
        session.execute(
            delete(orm.ProjectMember).where(
                orm.ProjectMember.project_id.in_(new_project_ids)
            )
        )
        session.execute(
            delete(orm.ThreadProjectAssignment).where(
                orm.ThreadProjectAssignment.project_id.in_(new_project_ids)
            )
        )

    member_rows: list[dict] = []
    for p in clustering.projects:
        member_rows.extend(mappers.project_member_rows(p))
    _upsert(session, orm.ProjectMember, member_rows, ["project_id", "person_id"])

    assignment_rows = [mappers.assignment_to_row(a) for a in clustering.assignments]
    _upsert(
        session, orm.ThreadProjectAssignment, assignment_rows, ["thread_id", "project_id"]
    )
