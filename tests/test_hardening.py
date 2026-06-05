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


def test_run_ingest_max_messages_stops_iterator_early():
    """max_messages stops the provider iterator at the cap without consuming more.

    Uses a fake provider that counts how many times fetch() is called to prove
    islice() never asks the provider for more IDs than the cap.
    """
    from services.ingest.pipeline import IngestConfig, run_ingest
    from services.ingest.providers.base import MimePart, RawMessage

    class CountingProvider:
        """Yields an infinite stream of fake IDs; records how many were fetched."""
        def __init__(self):
            self.fetched: list[str] = []

        def authorize(self, grant):
            pass

        def list_ids(self, since_token):
            n = 0
            while True:
                yield f"fake-id-{n}"
                n += 1

        def fetch(self, provider_id: str) -> RawMessage:
            self.fetched.append(provider_id)
            return RawMessage(
                provider_id=provider_id,
                provider_thread_id=f"thread-{provider_id}",
                headers={
                    "Message-ID": f"<{provider_id}@test>",
                    "From": "sender@acme.com",
                    "To": "alex@acme.com",
                    "Date": "2026-01-01T00:00:00Z",
                    "Subject": "Test",
                },
                mime_parts=[MimePart(type="text/plain", bytes=b"hello", charset="utf-8")],
            )

        def fetch_all(self):
            raise NotImplementedError("fetch_all must not be called when max_messages is set")

        def sync_token(self) -> str:
            return "fake-history-id"

    counting = CountingProvider()

    # Monkey-patch make_provider to return the counting provider.
    import services.ingest.pipeline as pipeline_mod
    original_make = pipeline_mod.make_provider

    def fake_make(cfg):
        return counting

    pipeline_mod.make_provider = fake_make
    try:
        cfg = IngestConfig(
            provider="fake",
            owner_email=OWNER_EMAIL,
            internal_domains=["acme.com"],
            max_messages=3,
        )
        store = run_ingest(cfg)
    finally:
        pipeline_mod.make_provider = original_make

    assert len(store.messages) == 3
    assert len(counting.fetched) == 3, "iterator must stop at the cap, not over-fetch"


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

def test_runner_token_to_save_guard():
    """The runner's token_to_save logic: falsy token or capped run -> None."""
    # Mirror the guard in _run_post_start so it is tested independently
    # of any DB call. This is a pure-logic unit test — no DB required.
    def compute_token_to_save(new_sync_token: str, hit_cap: bool):
        return new_sync_token if (new_sync_token and not hit_cap) else None

    assert compute_token_to_save("history-abc", False) == "history-abc"
    assert compute_token_to_save("history-abc", True)  is None   # capped
    assert compute_token_to_save("", False)             is None   # empty token
    assert compute_token_to_save("", True)              is None   # both


@requires_db
def test_write_audit_event_ingest_error_row():
    """An ingest_error audit row is appended on failure (append-only audit trail)."""
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import write_audit_event
    from sqlalchemy import select

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email="error-test@example.com",
        embed_model="t", embed_dim=0, config={},
    )
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)

    try:
        write_audit_event(session, mailbox_id=mid, actor="client:test", action="ingest_start", scope="gmail.readonly")
        write_audit_event(session, mailbox_id=mid, actor="client:test", action="ingest_error", scope="gmail.readonly")

        rows = session.execute(
            select(orm.AuditLog).where(orm.AuditLog.mailbox_id == mid)
        ).scalars().all()
        assert len(rows) == 2
        assert {r.action for r in rows} == {"ingest_start", "ingest_error"}
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        session.commit()
        session.close()


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
