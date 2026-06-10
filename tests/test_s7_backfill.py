"""S7.5 — embed_backfill tests (offline + DB-required).

Offline tests: content_hash correctness, filtering logic (already-embedded),
dry-run behaviour, and zero-API-call idempotency — all using FakeEmbedClient
and a lightweight fake session so no DB is needed.

DB-required tests: end-to-end upsert and idempotency against a live Postgres.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from unittest.mock import MagicMock

import pytest

from services.retrieval.embed_client import FakeEmbedClient

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


_requires_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres unreachable (run `docker compose up -d`).",
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_msg(msg_id: str, subject: str = "Hello", clean_text: str = "Body") -> dict:
    return {"id": msg_id, "subject": subject, "clean_text": clean_text}


def _hash(subject: str, clean_text: str) -> str:
    payload = subject + "\n\n" + clean_text
    return hashlib.sha256(payload.encode()).hexdigest()


class _FakeExec:
    """Minimal stand-in for session.execute(...).mappings().all() / .all()."""
    def __init__(self, rows):
        self._rows = rows
    def mappings(self):
        return self
    def all(self):
        return self._rows


class _FakeSession:
    """Injects canned DB responses for offline backfill tests."""
    def __init__(self, candidate_rows: list[dict], existing_rows: list[tuple]):
        self._candidates = candidate_rows
        self._existing   = existing_rows
        self.upsert_calls: list = []
        self.commit_count = 0

    def execute(self, stmt, params=None):
        stmt_str = str(stmt)
        if "FROM message_embedding" in stmt_str:
            return _FakeExec(self._existing)
        if "FROM message" in stmt_str:
            return _FakeExec(self._candidates)
        # Upsert (INSERT INTO message_embedding)
        self.upsert_calls.append((stmt, params))
        return _FakeExec([])

    def commit(self):
        self.commit_count += 1

    def close(self):
        pass


# ── _DryRunEmbedClient ────────────────────────────────────────────────────────

def test_dry_run_client_has_correct_model():
    from scripts.embed_backfill import _DryRunEmbedClient
    c = _DryRunEmbedClient(model="voyage-4")
    assert c.model == "voyage-4"
    assert c.dim   == 1024


def test_dry_run_client_raises_on_embed_documents():
    from scripts.embed_backfill import _DryRunEmbedClient
    c = _DryRunEmbedClient(model="voyage-4")
    with pytest.raises(RuntimeError, match="dry-run"):
        c.embed_documents(["text"])


def test_dry_run_main_does_not_require_voyage_key(monkeypatch, tmp_path):
    """--dry-run must succeed even when VOYAGE_API_KEY is absent."""
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    # run_backfill() needs a real session to talk to the DB, but we only
    # want to test the CLI-level key bypass.  Patch run_backfill itself.
    import scripts.embed_backfill as mod
    calls = []

    def _fake_run(**kwargs):
        calls.append(kwargs)
        return {"to_embed": 0, "embedded": 0, "total_messages": 0,
                "already_embedded": 0, "est_tokens": 0, "est_cost_usd": 0.0,
                "model": "voyage-4"}

    monkeypatch.setattr(mod, "run_backfill", _fake_run)

    mod.main(["--mailbox-id", str(uuid.uuid4()), "--dry-run", "--confirm"])

    assert calls, "run_backfill should have been called"
    client = calls[0]["embed_client"]
    assert isinstance(client, mod._DryRunEmbedClient)


# ── content_hash ──────────────────────────────────────────────────────────────

def test_content_hash_matches_sha256():
    from scripts.embed_backfill import content_hash

    subject = "Re: Project Alpha"
    body    = "Please review the attached proposal."
    expected = hashlib.sha256((subject + "\n\n" + body).encode()).hexdigest()
    assert content_hash(subject, body) == expected


def test_content_hash_differs_for_different_text():
    from scripts.embed_backfill import content_hash

    assert content_hash("A", "B") != content_hash("A", "C")


def test_content_hash_is_deterministic():
    from scripts.embed_backfill import content_hash

    h1 = content_hash("Subject", "Body")
    h2 = content_hash("Subject", "Body")
    assert h1 == h2


# ── dry_run — no DB writes ────────────────────────────────────────────────────

def test_dry_run_makes_no_db_writes():
    from scripts.embed_backfill import run_backfill

    mid   = str(uuid.uuid4())
    msgs  = [_make_msg(str(uuid.uuid4()), "S1", "B1")]
    sess  = _FakeSession(candidate_rows=msgs, existing_rows=[])
    client = FakeEmbedClient(dim=8)

    stats = run_backfill(
        mailbox_id=mid,
        embed_client=client,
        dry_run=True,
        confirm=True,
        session=sess,
    )

    assert stats["to_embed"] == 1
    assert sess.commit_count == 0           # no writes
    assert sess.upsert_calls == []


def test_dry_run_returns_correct_counts():
    from scripts.embed_backfill import run_backfill

    mid  = str(uuid.uuid4())
    msgs = [_make_msg(str(uuid.uuid4()), "S", "B") for _ in range(5)]
    sess = _FakeSession(candidate_rows=msgs, existing_rows=[])

    stats = run_backfill(
        mailbox_id=mid,
        embed_client=FakeEmbedClient(dim=8),
        dry_run=True,
        confirm=True,
        session=sess,
    )

    assert stats["total_messages"] == 5
    assert stats["to_embed"] == 5
    assert stats["already_embedded"] == 0


# ── idempotency — all hashes match ───────────────────────────────────────────

def test_all_matching_hashes_skip_embed():
    """Re-running when all content_hashes match produces zero API calls."""
    from scripts.embed_backfill import content_hash, run_backfill

    mid  = str(uuid.uuid4())
    msgs = [_make_msg(str(uuid.uuid4()), f"S{i}", f"B{i}") for i in range(3)]
    existing = [(m["id"], content_hash(m["subject"], m["clean_text"])) for m in msgs]

    sess = _FakeSession(candidate_rows=msgs, existing_rows=existing)

    embed_calls: list = []
    client = FakeEmbedClient(dim=8)
    real_embed = client.embed_documents

    def spy(texts):
        embed_calls.append(texts)
        return real_embed(texts)

    client.embed_documents = spy  # type: ignore[method-assign]

    stats = run_backfill(
        mailbox_id=mid,
        embed_client=client,
        dry_run=False,
        confirm=True,
        session=sess,
    )

    assert stats["to_embed"] == 0
    assert embed_calls == []   # embed_documents never called


def test_changed_hash_triggers_embed():
    """A message whose content_hash has changed must be re-embedded."""
    from scripts.embed_backfill import run_backfill

    mid = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    msg = _make_msg(msg_id, "Old subject", "Old body")

    # Existing hash is for *different* text — simulates content change.
    stale_hash = _hash("Old subject", "Different body")
    existing   = [(msg_id, stale_hash)]

    sess = _FakeSession(candidate_rows=[msg], existing_rows=existing)

    stats = run_backfill(
        mailbox_id=mid,
        embed_client=FakeEmbedClient(dim=8),
        dry_run=False,
        confirm=True,
        session=sess,
    )

    assert stats["to_embed"] == 1
    assert stats.get("embedded", 0) == 1


# ── stats counts ─────────────────────────────────────────────────────────────

def test_stats_already_embedded_count():
    from scripts.embed_backfill import content_hash, run_backfill

    mid  = str(uuid.uuid4())
    msgs = [_make_msg(str(uuid.uuid4()), f"S{i}", f"B{i}") for i in range(4)]

    # First two are already embedded with correct hashes.
    existing = [(m["id"], content_hash(m["subject"], m["clean_text"])) for m in msgs[:2]]
    sess     = _FakeSession(candidate_rows=msgs, existing_rows=existing)

    stats = run_backfill(
        mailbox_id=mid,
        embed_client=FakeEmbedClient(dim=8),
        dry_run=False,
        confirm=True,
        session=sess,
    )

    assert stats["total_messages"]   == 4
    assert stats["already_embedded"] == 2
    assert stats["to_embed"]         == 2
    assert stats.get("embedded", 0)  == 2


# ── DB-required: upsert round-trip ───────────────────────────────────────────

@_requires_db
def test_backfill_upsert_and_idempotency():
    """Run backfill on a seeded mailbox; second run makes zero API calls."""
    from sqlalchemy import select, text

    from services.db.engine import SessionLocal
    from services.db import models as orm

    session = SessionLocal()
    try:
        # Create an isolated mailbox + one message.
        mbx = orm.Mailbox(
            provider="gmail",
            owner_email="backfill_test@example.com",
            status="active",
            embed_model="fake-embed",
            embed_dim=1024,
            config={},
        )
        session.add(mbx)
        session.flush()
        mailbox_id = str(mbx.id)

        thread = orm.Thread(
            mailbox_id=mailbox_id,
            provider_thread_ids=[],
            root_message_id_header="<root@test>",
            subject_norm="test thread",
            participants=["a@test.com"],
            t_start=__import__("datetime").datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            t_end=__import__("datetime").datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            lineage_conflict=False,
        )
        session.add(thread)
        session.flush()

        msg = orm.Message(
            mailbox_id=mailbox_id,
            message_id_header="<backfill-test@example.com>",
            provider_id="provider-001",
            thread_id=str(thread.id),
            sender_email="a@test.com",
            to_emails=["b@test.com"],
            cc_emails=[],
            addresses={},
            ts=__import__("datetime").datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            subject="Backfill test subject",
            clean_text="Backfill test body content.",
            link_domains=[],
            sensitivity=["none"],
            noise=False,
        )
        session.add(msg)
        session.commit()

        client = FakeEmbedClient(dim=1024, model="fake-embed")

        # First run: should embed the one message.
        stats1 = run_backfill(
            mailbox_id=mailbox_id,
            embed_client=client,
            dry_run=False,
            confirm=True,
            session=session,
        )
        assert stats1["to_embed"] == 1
        assert stats1.get("embedded", 0) == 1

        # Verify row exists in DB.
        row = session.execute(
            select(orm.MessageEmbedding).where(
                orm.MessageEmbedding.mailbox_id == mailbox_id
            )
        ).scalar_one()
        assert len(row.embedding) == 1024

        # Second run: all hashes match → zero embeds, no new API calls.
        embed_calls: list = []
        real_embed = client.embed_documents

        def spy(texts):
            embed_calls.append(texts)
            return real_embed(texts)

        client.embed_documents = spy  # type: ignore[method-assign]

        stats2 = run_backfill(
            mailbox_id=mailbox_id,
            embed_client=client,
            dry_run=False,
            confirm=True,
            session=session,
        )
        assert stats2["to_embed"] == 0
        assert embed_calls == [], "Second run must not call embed_documents"

    finally:
        session.rollback()
        session.close()


# Import here so the DB test can use it without re-importing inside the function.
from scripts.embed_backfill import run_backfill  # noqa: E402
