"""S7.7 — fts_search tests.

Offline tests validate the empty-query short-circuit and RetrievalHit field
invariants without a DB.

DB-required tests run the full fts_search against a live Postgres with
migration 0006 applied (subject_clean_tsv column + GIN index).  All DB tests
use the fixture mailbox data seeded in conftest rather than creating isolated
mailboxes, so they depend on dev_seed having been run; individual tests that
need specific content create their own isolated mailbox rows instead.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from services.retrieval.contracts import RetrievalHit
from services.retrieval.params import RetrievalParams

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

_DIM = 1024


# ── Offline: empty-query short-circuit ───────────────────────────────────────

def test_empty_query_returns_empty_without_db():
    """fts_search must short-circuit on empty input without touching the DB."""
    from unittest.mock import MagicMock
    from services.retrieval.fts import fts_search

    params = RetrievalParams()
    result = fts_search(MagicMock(), "some-mailbox", "", params)
    assert result == []


def test_whitespace_only_query_returns_empty_without_db():
    from unittest.mock import MagicMock
    from services.retrieval.fts import fts_search

    params = RetrievalParams()
    result = fts_search(MagicMock(), "some-mailbox", "   \t\n  ", params)
    assert result == []


# ── Offline: RetrievalHit field invariants ───────────────────────────────────

def test_fts_hit_has_fts_score_and_no_vector_score():
    hit = RetrievalHit(
        message_id="m1",
        message_id_header="m1@x",
        thread_id="t1",
        project_ids=(),
        person_ids=(),
        ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
        subject="Test",
        snippet="snippet",
        vector_score=None,
        fts_score=0.42,
        rerank_score=0.42,
        source="fts",
        sensitivity=("none",),
        noise=False,
    )
    assert hit.source == "fts"
    assert hit.vector_score is None
    assert hit.fts_score == 0.42


# ── DB fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture()
def session():
    from services.db.engine import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed_fts_mailbox(session, messages: list[tuple[str, str]]) -> str:
    """Create an isolated mailbox with the given (subject, clean_text) pairs.

    Returns mailbox_id.  The caller's session is used and must be committed by
    the fixture teardown (rollback is fine for isolation).
    """
    from services.db import models as orm

    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=f"fts_{uuid.uuid4().hex[:8]}@example.com",
        status="active",
        embed_model="fake-embed",
        embed_dim=_DIM,
        config={},
    )
    session.add(mbx)
    session.flush()
    mailbox_id = str(mbx.id)

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    thread = orm.Thread(
        mailbox_id=mailbox_id,
        provider_thread_ids=[],
        root_message_id_header=f"fts-root-{mailbox_id[:8]}@x",
        subject_norm="fts test thread",
        participants=["a@test.com"],
        t_start=t0, t_end=t0, lineage_conflict=False,
    )
    session.add(thread)
    session.flush()
    thread_id = str(thread.id)

    for i, (subj, body) in enumerate(messages):
        msg = orm.Message(
            mailbox_id=mailbox_id,
            message_id_header=f"fts-{mailbox_id[:8]}-{i}@example.com",
            provider_id=f"p-fts-{i}",
            thread_id=thread_id,
            sender_email="a@test.com",
            to_emails=["b@test.com"],
            cc_emails=[],
            addresses={},
            ts=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            subject=subj,
            clean_text=body,
            link_domains=[],
            sensitivity=["none"],
            noise=False,
        )
        session.add(msg)

    session.commit()
    return mailbox_id


# ── DB integration tests ───────────────────────────────────────────────────────

@_requires_db
def test_fts_search_finds_keyword_in_subject(session):
    from services.retrieval.fts import fts_search

    mailbox_id = _seed_fts_mailbox(session, [
        ("Atlas rollout schedule", "Please review the attached timeline."),
        ("Quarterly budget review", "Finance team meeting notes are attached."),
    ])
    params = RetrievalParams()
    hits = fts_search(session, mailbox_id, "atlas", params)

    assert len(hits) == 1
    assert "atlas" in hits[0].subject.lower()


@_requires_db
def test_fts_search_finds_keyword_in_body(session):
    from services.retrieval.fts import fts_search

    mailbox_id = _seed_fts_mailbox(session, [
        ("Weekly update", "The atlas deployment is now complete."),
        ("Finance notes", "Budget reconciliation is done."),
    ])
    params = RetrievalParams()
    hits = fts_search(session, mailbox_id, "deployment", params)

    assert len(hits) >= 1
    assert any("deployment" in h.snippet.lower() for h in hits)


@_requires_db
def test_fts_returns_hits_with_fts_score_not_vector(session):
    from services.retrieval.fts import fts_search

    mailbox_id = _seed_fts_mailbox(session, [
        ("Project alpha status", "We shipped the feature last week."),
    ])
    params = RetrievalParams()
    hits = fts_search(session, mailbox_id, "project alpha", params)

    assert all(isinstance(h, RetrievalHit) for h in hits)
    assert all(h.fts_score is not None for h in hits)
    assert all(h.vector_score is None for h in hits)
    assert all(h.source == "fts" for h in hits)


@_requires_db
def test_fts_results_sorted_descending_by_fts_score(session):
    from services.retrieval.fts import fts_search

    # Use three messages with different amounts of the search term
    # so ts_rank produces meaningfully different scores.
    mailbox_id = _seed_fts_mailbox(session, [
        ("Atlas", "Unrelated content."),
        ("Atlas rollout", "The atlas project is running."),
        ("Atlas rollout deployment", "Atlas is our main deployment platform for atlas services."),
    ])
    params = RetrievalParams(fts_top_k=10)
    hits = fts_search(session, mailbox_id, "atlas", params)

    assert len(hits) >= 2
    scores = [h.fts_score for h in hits]
    assert scores == sorted(scores, reverse=True), "FTS hits must be sorted descending"


@_requires_db
def test_fts_stop_word_only_query_returns_empty(session):
    """'the' is an English stop word; websearch_to_tsquery returns NULL → no rows."""
    from services.retrieval.fts import fts_search

    mailbox_id = _seed_fts_mailbox(session, [
        ("The atlas rollout", "The deployment is done."),
    ])
    params = RetrievalParams()
    # 'the' is a stop word; expect empty result, not an error.
    hits = fts_search(session, mailbox_id, "the", params)
    assert hits == []


@_requires_db
def test_fts_no_match_returns_empty(session):
    from services.retrieval.fts import fts_search

    mailbox_id = _seed_fts_mailbox(session, [
        ("Budget review", "Finance team quarterly notes."),
    ])
    params = RetrievalParams()
    hits = fts_search(session, mailbox_id, "xyzzy_nonexistent_token", params)
    assert hits == []


@_requires_db
def test_fts_noise_filter(session):
    """Noise messages must be excluded when include_noise=False."""
    from services.db import models as orm
    from services.retrieval.fts import fts_search

    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=f"fts_noise_{uuid.uuid4().hex[:8]}@example.com",
        status="active",
        embed_model="fake-embed",
        embed_dim=_DIM,
        config={},
    )
    session.add(mbx)
    session.flush()
    mailbox_id = str(mbx.id)

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    thread = orm.Thread(
        mailbox_id=mailbox_id, provider_thread_ids=[],
        root_message_id_header="fts-noise-root@x", subject_norm="noise",
        participants=["a@test.com"], t_start=t0, t_end=t0, lineage_conflict=False,
    )
    session.add(thread)
    session.flush()

    # Noise message — should not appear in results
    session.add(orm.Message(
        mailbox_id=mailbox_id,
        message_id_header="fts-noise@example.com",
        provider_id="p-noise", thread_id=str(thread.id),
        sender_email="a@test.com", to_emails=[], cc_emails=[], addresses={},
        ts=t0, subject="Atlas unsubscribe notification",
        clean_text="You have been unsubscribed from atlas updates.",
        link_domains=[], sensitivity=["none"], noise=True,
    ))
    session.commit()

    params = RetrievalParams(include_noise=False)
    hits = fts_search(session, mailbox_id, "atlas", params)
    assert all(not h.noise for h in hits)


@_requires_db
def test_fts_sensitivity_filter(session):
    """Sensitive messages must be excluded when include_sensitive=False."""
    from services.db import models as orm
    from services.retrieval.fts import fts_search

    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=f"fts_sens_{uuid.uuid4().hex[:8]}@example.com",
        status="active",
        embed_model="fake-embed",
        embed_dim=_DIM,
        config={},
    )
    session.add(mbx)
    session.flush()
    mailbox_id = str(mbx.id)

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    thread = orm.Thread(
        mailbox_id=mailbox_id, provider_thread_ids=[],
        root_message_id_header="fts-sens-root@x", subject_norm="sensitive",
        participants=["hr@example.com"], t_start=t0, t_end=t0, lineage_conflict=False,
    )
    session.add(thread)
    session.flush()

    session.add(orm.Message(
        mailbox_id=mailbox_id,
        message_id_header="fts-sens@example.com",
        provider_id="p-sens", thread_id=str(thread.id),
        sender_email="hr@example.com", to_emails=[], cc_emails=[], addresses={},
        ts=t0, subject="Atlas salary review",
        clean_text="Compensation details for atlas team members.",
        link_domains=[], sensitivity=["hr"], noise=False,
    ))
    session.commit()

    params = RetrievalParams(include_sensitive=False)
    hits = fts_search(session, mailbox_id, "atlas salary", params)
    assert all("hr" not in h.sensitivity for h in hits)


@_requires_db
def test_fts_snippet_truncated_to_300_chars(session):
    long_body = "atlas " + ("x " * 200)
    mailbox_id = _seed_fts_mailbox(session, [("Atlas rollout", long_body)])
    from services.retrieval.fts import fts_search
    params = RetrievalParams()
    hits = fts_search(session, mailbox_id, "atlas", params)
    assert hits
    assert len(hits[0].snippet) <= 300


@_requires_db
def test_fts_all_headers_in_db(session):
    """Citation contract: every returned message_id_header must exist in DB."""
    from sqlalchemy import select
    from services.db import models as orm
    from services.retrieval.fts import fts_search

    mailbox_id = _seed_fts_mailbox(session, [
        ("Atlas planning meeting", "Discussing the atlas roadmap."),
        ("Atlas rollout status", "Atlas deployment is 80% done."),
    ])
    params = RetrievalParams(fts_top_k=10)
    hits = fts_search(session, mailbox_id, "atlas", params)

    for hit in hits:
        row = session.execute(
            select(orm.Message).where(
                orm.Message.mailbox_id == mailbox_id,
                orm.Message.message_id_header == hit.message_id_header,
            )
        ).scalar_one_or_none()
        assert row is not None, f"header {hit.message_id_header!r} not found in DB"
