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

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from services.enrich.pipeline import EnrichResult
from services.ingest.store import IngestStore

from . import mappers
from . import models as orm


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


def persist_l0(store: IngestStore, mailbox_id: str, session: Session) -> None:
    """Upsert threads then messages (+ attachments). Idempotent on message_id_header."""
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
    """Upsert orgs, persons, identities, edges. Idempotent on id / natural keys."""
    org_rows = [mappers.org_to_row(o, mailbox_id) for o in result.orgs]
    _upsert(session, orm.Org, org_rows, ["id"])

    person_rows = [mappers.person_to_row(p, mailbox_id) for p in result.people]
    _upsert(session, orm.Person, person_rows, ["id"])

    identity_rows = [mappers.identity_to_row(i, mailbox_id) for i in result.identities]
    _upsert(session, orm.Identity, identity_rows, ["mailbox_id", "email"])

    edge_rows = [mappers.edge_to_row(e, mailbox_id) for e in result.edges]
    _upsert(session, orm.Edge, edge_rows, ["mailbox_id", "person_id"])

    session.commit()
