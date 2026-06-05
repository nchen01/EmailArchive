"""Tests for production-hardening-demo additions.

Covers:
  - run_ingest since_token / max_messages via FixtureProvider
  - write_audit_event / save_sync_token / load_sync_token DB helpers
"""
from __future__ import annotations

import os
import uuid

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")


def _db_reachable() -> bool:
    if not DATABASE_URL:
        return False
    try:
        from services.db.engine import engine
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres unreachable.",
)

OWNER_EMAIL = "alex@acme.com"
FIXTURE_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "fixtures"


# ── run_ingest with cap/since_token (FixtureProvider, no DB needed) ─────────

def test_run_ingest_max_messages_caps_fetch():
    """max_messages=1 fetches exactly one message without enumerating the full fixture."""
    from services.ingest.pipeline import IngestConfig, run_ingest

    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=FIXTURE_DIR / "mailbox.json",
        owner_email=OWNER_EMAIL,
        internal_domains=["acme.com"],
        max_messages=1,
    )
    store = run_ingest(cfg)
    assert len(store.messages) == 1


def test_run_ingest_max_messages_zero_returns_empty():
    """max_messages=0 results in no messages."""
    from services.ingest.pipeline import IngestConfig, run_ingest

    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=FIXTURE_DIR / "mailbox.json",
        owner_email=OWNER_EMAIL,
        internal_domains=["acme.com"],
        max_messages=0,
    )
    store = run_ingest(cfg)
    assert len(store.messages) == 0


def test_run_ingest_max_messages_larger_than_fixture_returns_all():
    """max_messages > fixture size returns all fixture messages."""
    from services.ingest.pipeline import IngestConfig, run_ingest

    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=FIXTURE_DIR / "mailbox.json",
        owner_email=OWNER_EMAIL,
        internal_domains=["acme.com"],
        max_messages=9999,
    )
    store = run_ingest(cfg)
    assert len(store.messages) == 18  # full fixture


def test_run_ingest_since_token_fixture_ignores_token():
    """FixtureProvider ignores since_token (it has no incremental state).
    The path still works without error and returns fixture messages."""
    from services.ingest.pipeline import IngestConfig, run_ingest

    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=FIXTURE_DIR / "mailbox.json",
        owner_email=OWNER_EMAIL,
        internal_domains=["acme.com"],
        since_token="fake-history-id-12345",
    )
    store = run_ingest(cfg)
    # FixtureProvider.list_ids ignores since_token; still returns all ids.
    assert len(store.messages) == 18


def test_run_ingest_since_token_and_max_messages_combined():
    """since_token + max_messages together: only the cap takes effect on fixture."""
    from services.ingest.pipeline import IngestConfig, run_ingest

    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=FIXTURE_DIR / "mailbox.json",
        owner_email=OWNER_EMAIL,
        internal_domains=["acme.com"],
        since_token="any-token",
        max_messages=5,
    )
    store = run_ingest(cfg)
    assert len(store.messages) == 5


# ── audit log / sync state DB helpers ────────────────────────────────────────

@requires_db
def test_write_audit_event_appends_two_rows():
    """write_audit_event creates two immutable rows (start + finish) per run."""
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import write_audit_event
    from sqlalchemy import select

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email="audit-test@example.com",
        embed_model="t", embed_dim=0, config={},
    )
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)

    try:
        write_audit_event(session, mailbox_id=mid, actor="client:test", action="ingest_start", scope="gmail.readonly")
        write_audit_event(session, mailbox_id=mid, actor="client:test", action="ingest_finish", scope="gmail.readonly", message_count=42)

        rows = session.execute(
            select(orm.AuditLog).where(orm.AuditLog.mailbox_id == mid)
        ).scalars().all()
        assert len(rows) == 2
        actions = {r.action for r in rows}
        assert actions == {"ingest_start", "ingest_finish"}
        finish = next(r for r in rows if r.action == "ingest_finish")
        assert finish.message_count == 42
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        session.commit()
        session.close()


@requires_db
def test_audit_log_rows_are_not_updated():
    """Audit rows must be immutable — no UPDATE path exists in write_audit_event."""
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import write_audit_event
    from sqlalchemy import select

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email="immutable-test@example.com",
        embed_model="t", embed_dim=0, config={},
    )
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)

    try:
        write_audit_event(session, mailbox_id=mid, actor="client:x", action="ingest_start")
        write_audit_event(session, mailbox_id=mid, actor="client:x", action="ingest_start")  # second row
        rows = session.execute(
            select(orm.AuditLog).where(orm.AuditLog.mailbox_id == mid)
        ).scalars().all()
        # Two separate rows, not one updated row.
        assert len(rows) == 2
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        session.commit()
        session.close()


@requires_db
def test_save_and_load_sync_token_roundtrip():
    """save_sync_token persists and load_sync_token retrieves the historyId."""
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import load_sync_token, save_sync_token

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email="sync-test@example.com",
        embed_model="t", embed_dim=0, config={},
    )
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)

    try:
        assert load_sync_token(session, mid) is None  # no token yet
        save_sync_token(session, mid, "history-12345")
        assert load_sync_token(session, mid) == "history-12345"
        # Update is idempotent.
        save_sync_token(session, mid, "history-99999")
        assert load_sync_token(session, mid) == "history-99999"
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        session.commit()
        session.close()
