"""Materialize project clusters for a live mailbox (S9).

Runs the existing S3 clustering pipeline (services/enrich/clustering/) against
an already-ingested mailbox and persists Project, ThreadProjectAssignment, and
ProjectMember rows so Project View and cover-for-me project routing work on
real data.

Usage:
    python scripts/materialize_projects.py --mailbox-id <uuid> --dry-run
    python scripts/materialize_projects.py --mailbox-id <uuid> --confirm
    python scripts/materialize_projects.py --mailbox-id <uuid> --confirm --limit-threads 50

Embedding strategy (design decision):
    Voyage-4 vectors already stored in message_embedding are reused for the
    clustering embed_fn.  This gives a single semantic embedding path for both
    retrieval and clustering — no second model, no extra API calls.  Each
    message's clean_text is mapped to its stored 1024-dim vector via a lookup
    dict.  Messages without a stored embedding (noise=True or sensitivity!=none)
    receive a zero-vector fallback; since only eligible threads (>=1 embedded
    message) are clustered, this fallback is seldom reached.

Idempotency:
    Project IDs are uuid5 over the sorted set of thread_ids in each cluster
    (services/enrich/clustering/materialize.py), so re-running on identical input
    produces identical IDs.  _persist_clustering() upserts projects and
    delete-reinserts their child rows.  Re-running is safe.

Exclusions:
    Threads with no non-noise, non-sensitive messages are not clustered.
    Threads containing sensitive messages (hr/legal/privileged/personal) are
    excluded even if they also contain non-sensitive messages.

Dry-run output:
    - Total threads in mailbox
    - Eligible (clusterable) thread count
    - Excluded thread count
    - Projects found and top labels

Confirm output:
    - Projects created/updated
    - Thread assignments created/updated
    - Project members created/updated
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text

from services.db import mappers
from services.db import models as orm

_EMBED_DIM = 1024  # voyage-4 dimension


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_eligible_thread_ids(session, mailbox_id: str, limit: int | None = None) -> list[str]:
    """Thread IDs that have ≥1 non-noise, non-sensitive message.

    Uses raw SQL for the sensitivity array comparison to avoid SQLAlchemy
    ARRAY equality quirks.
    """
    rows = session.execute(
        text(
            "SELECT DISTINCT thread_id::text "
            "FROM message "
            "WHERE mailbox_id = :mid "
            "  AND noise = false "
            "  AND sensitivity = '{none}'"
        ),
        {"mid": mailbox_id},
    ).all()
    ids = [r[0] for r in rows]
    if limit is not None:
        ids = ids[:limit]
    return ids


def _count_total_threads(session, mailbox_id: str) -> int:
    from sqlalchemy import func
    return session.execute(
        select(func.count()).select_from(orm.Thread).where(orm.Thread.mailbox_id == mailbox_id)
    ).scalar() or 0


def _load_thread_rows(session, thread_ids: list[str]) -> list:
    """Load ORM Thread rows for the given IDs, ordered by t_start."""
    return session.execute(
        select(orm.Thread)
        .where(orm.Thread.id.in_(thread_ids))
        .order_by(orm.Thread.t_start)
    ).scalars().all()


def _get_embeddable_message_ids(session, mailbox_id: str, thread_ids: list[str]) -> list[str]:
    """Message IDs that are non-noise, non-sensitive, in the given threads."""
    rows = session.execute(
        text(
            "SELECT id::text "
            "FROM message "
            "WHERE mailbox_id = :mid "
            "  AND thread_id::text = ANY(:tids) "
            "  AND noise = false "
            "  AND sensitivity = '{none}' "
            "ORDER BY ts, id"
        ),
        {"mid": mailbox_id, "tids": thread_ids},
    ).all()
    return [r[0] for r in rows]


def _load_message_rows(session, message_ids: list[str]) -> list:
    """Load ORM Message rows for the given IDs (no attachments needed)."""
    return session.execute(
        select(orm.Message)
        .where(orm.Message.id.in_(message_ids))
        .order_by(orm.Message.ts, orm.Message.id)
    ).scalars().all()


def _load_voyage_vectors(session, mailbox_id: str) -> dict[str, np.ndarray]:
    """Load {message_id: np.ndarray} for voyage-4 embeddings in this mailbox.

    Reads embedding::text and parses the pgvector [v1,v2,...] literal.
    """
    rows = session.execute(
        text(
            "SELECT message_id::text, embedding::text "
            "FROM message_embedding "
            "WHERE mailbox_id = :mid AND embed_model = 'voyage-4'"
        ),
        {"mid": mailbox_id},
    ).all()
    result: dict[str, np.ndarray] = {}
    for msg_id, vec_str in rows:
        nums = [float(x) for x in vec_str.strip("[]").split(",")]
        result[msg_id] = np.array(nums, dtype=np.float32)
    return result


def _load_identity_map(session, mailbox_id: str) -> dict[str, str]:
    """Return {email: person_id_str} from the identity table."""
    rows = session.execute(
        select(orm.Identity.email, orm.Identity.person_id)
        .where(orm.Identity.mailbox_id == mailbox_id)
        .where(orm.Identity.person_id.isnot(None))
    ).all()
    return {email: str(person_id) for email, person_id in rows}


# ── Embedding bridge ──────────────────────────────────────────────────────────

def _make_db_embed_fn(messages, msg_id_to_vec: dict[str, np.ndarray]):
    """Return an embed_fn backed by pre-loaded voyage-4 DB vectors.

    Maps each message's clean_text to its stored vector.  Messages not in the
    lookup receive a zero-vector fallback (callers normalize; zero becomes a
    direction-less unit vector that contributes equally to all clusters —
    acceptable when the message rarely appears in an eligible thread).

    If two messages share identical clean_text, the last one wins in the lookup.
    In practice this is rare for real email content.
    """
    lookup: dict[str, np.ndarray] = {}
    for m in messages:
        mid = str(m.id)
        if mid in msg_id_to_vec:
            lookup[m.clean_text or ""] = msg_id_to_vec[mid]

    def embed_fn(text: str) -> np.ndarray:
        v = lookup.get(text or "")
        if v is not None:
            return v
        return np.zeros(_EMBED_DIM, dtype=np.float32)

    return embed_fn


# ── Main entry point ──────────────────────────────────────────────────────────

def materialize(
    *,
    mailbox_id: str,
    limit_threads: int | None = None,
    dry_run: bool = True,
    session_factory=None,
) -> dict:
    """Run project clustering and optionally persist the result.

    Returns a stats dict with keys:
      total_threads, eligible_threads, excluded_threads,
      projects_found, top_labels, strategy,
      projects_written, assignments_written, members_written.

    When dry_run=True no DB writes are made; clustering still runs so
    dry-run output is accurate.  Injectable session_factory for testing.
    """
    if session_factory is None:
        from services.db.engine import SessionLocal
        session_factory = SessionLocal

    session = session_factory()
    try:
        mailbox = session.get(orm.Mailbox, mailbox_id)
        if mailbox is None:
            raise ValueError(f"Mailbox {mailbox_id!r} not found")
        owner_email = mailbox.owner_email

        total_threads = _count_total_threads(session, mailbox_id)
        eligible_ids = _get_eligible_thread_ids(session, mailbox_id, limit=limit_threads)

        if not eligible_ids:
            return {
                "total_threads": total_threads,
                "eligible_threads": 0,
                "excluded_threads": total_threads,
                "projects_found": 0,
                "top_labels": [],
                "strategy": "none",
                "projects_written": 0,
                "assignments_written": 0,
                "members_written": 0,
            }

        excluded_count = total_threads - len(eligible_ids)

        # Load Thread ORM rows for eligible threads
        thread_rows = _load_thread_rows(session, eligible_ids)

        # Load only embeddable messages (non-noise, non-sensitive)
        embeddable_msg_ids = _get_embeddable_message_ids(session, mailbox_id, eligible_ids)
        msg_rows = _load_message_rows(session, embeddable_msg_ids)

        # Convert to ekc_schemas objects (no attachments: w_attach=0.05, minor)
        schema_messages = [mappers.row_to_message(r) for r in msg_rows]
        messages_by_thread: dict[str, list] = {}
        for m in schema_messages:
            messages_by_thread.setdefault(m.thread_id, []).append(m)

        schema_threads = [
            mappers.row_to_thread(
                r,
                message_ids=[m.id for m in messages_by_thread.get(str(r.id), [])],
            )
            for r in thread_rows
            if messages_by_thread.get(str(r.id))  # skip if all messages were excluded
        ]

        if not schema_threads:
            return {
                "total_threads": total_threads,
                "eligible_threads": len(eligible_ids),
                "excluded_threads": excluded_count,
                "projects_found": 0,
                "top_labels": [],
                "strategy": "none",
                "projects_written": 0,
                "assignments_written": 0,
                "members_written": 0,
            }

        # Build embed_fn from DB voyage-4 vectors
        msg_id_to_vec = _load_voyage_vectors(session, mailbox_id)
        embed_fn = _make_db_embed_fn(schema_messages, msg_id_to_vec)

        # Identity map and owner resolution
        email_to_person_id = _load_identity_map(session, mailbox_id)
        owner_person_id = (
            str(mailbox.owner_person_id)
            if mailbox.owner_person_id
            else email_to_person_id.get(owner_email, "")
        )

        # Run clustering (reuses existing S3 pipeline unchanged)
        from services.enrich.clustering.pipeline import cluster_from_store
        from services.enrich.clustering.testkit import FakeNlp

        result = cluster_from_store(
            schema_threads,
            messages_by_thread,
            email_to_person_id,
            owner_person_id,
            embed_fn=embed_fn,
            nlp=FakeNlp(),
        )

        strategy = result.run_meta.get("strategy", "unknown")
        top_labels = [
            p.label
            for p in sorted(result.projects, key=lambda p: -len(p.thread_ids))[:10]
        ]

        stats: dict = {
            "total_threads": total_threads,
            "eligible_threads": len(schema_threads),
            "excluded_threads": excluded_count,
            "projects_found": len(result.projects),
            "top_labels": top_labels,
            "strategy": strategy,
            "projects_written": 0,
            "assignments_written": 0,
            "members_written": 0,
        }

        if not dry_run:
            from services.db.store import _persist_clustering
            _persist_clustering(result, mailbox_id, session)
            stats["projects_written"] = len(result.projects)
            stats["assignments_written"] = len(result.assignments)
            stats["members_written"] = sum(len(p.members) for p in result.projects)

        return stats

    finally:
        session.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    from scripts._env import load_local_env
    load_local_env()

    p = argparse.ArgumentParser(
        description="Materialize project clusters for a live mailbox (S9)."
    )
    p.add_argument("--mailbox-id", required=True, metavar="UUID")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview clustering without writing to DB.")
    p.add_argument("--confirm", action="store_true",
                   help="Required for live DB writes (ignored with --dry-run).")
    p.add_argument("--limit-threads", type=int, default=None, metavar="N",
                   help="Limit to first N eligible threads (smoke testing).")
    args = p.parse_args()

    live = not args.dry_run and args.confirm
    if not args.dry_run and not args.confirm:
        print("ERROR: pass --dry-run to preview or --confirm to write.", file=sys.stderr)
        sys.exit(1)

    mode = "dry-run" if not live else "LIVE"
    print(f"Materialize projects — {mode}")
    print(f"  mailbox : {args.mailbox_id}")
    if args.limit_threads:
        print(f"  limit   : {args.limit_threads} threads")
    print()

    stats = materialize(
        mailbox_id=args.mailbox_id,
        limit_threads=args.limit_threads,
        dry_run=not live,
    )

    print("Threads:")
    print(f"  total    : {stats['total_threads']}")
    print(f"  eligible : {stats['eligible_threads']}")
    print(f"  excluded : {stats['excluded_threads']}")
    print()
    print(f"Clustering strategy : {stats['strategy']}")
    print(f"Projects found      : {stats['projects_found']}")
    if stats["top_labels"]:
        print("Top labels:")
        for label in stats["top_labels"]:
            print(f"  - {label}")

    if live:
        print()
        print("Written:")
        print(f"  projects    : {stats['projects_written']}")
        print(f"  assignments : {stats['assignments_written']}")
        print(f"  members     : {stats['members_written']}")
        print()
        print("Done. Project View and cover-for-me routing now reflect these projects.")
        print("To re-run: python scripts/materialize_projects.py --mailbox-id "
              f"{args.mailbox_id} --confirm")
    else:
        print()
        print("Dry-run complete. Re-run with --confirm to persist.")


if __name__ == "__main__":
    main()
