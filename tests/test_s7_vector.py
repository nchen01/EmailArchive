"""S7.6 — vector_search tests.

Offline tests validate RetrievalHit shape, snippet truncation, and the
sort invariant without a DB.

DB-required tests run the full vector_search pipeline using FakeEmbedClient
(no Voyage API key needed) against a live Postgres with migration 0006 applied.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from services.retrieval.contracts import RetrievalHit
from services.retrieval.params import RetrievalParams

DATABASE_URL = os.environ.get("DATABASE_URL")

_DIM_OFFLINE = 8     # used only in purely offline tests (no DB column constraint)
_DIM_DB      = 1024  # must match the vector(1024) column in migration 0006


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

# ── Offline: shape and sort invariant (no DB) ─────────────────────────────────

def _make_hit(score: float) -> RetrievalHit:
    return RetrievalHit(
        message_id=str(uuid.uuid4()),
        message_id_header=f"{score}@x",
        thread_id=str(uuid.uuid4()),
        project_ids=(),
        person_ids=(),
        ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
        subject="S",
        snippet="",
        vector_score=score,
        fts_score=None,
        rerank_score=score,
        source="vector",
        sensitivity=("none",),
        noise=False,
    )


def _offline_client():
    from services.retrieval.embed_client import FakeEmbedClient
    return FakeEmbedClient(dim=_DIM_OFFLINE)


def _db_client():
    from services.retrieval.embed_client import FakeEmbedClient
    return FakeEmbedClient(dim=_DIM_DB, model="fake-embed")


def test_retrieval_hit_source_field():
    hit = _make_hit(0.8)
    assert hit.source == "vector"
    assert hit.fts_score is None
    assert hit.vector_score == 0.8


def test_snippet_truncated_to_300_chars():
    long_text = "x" * 500
    assert long_text[:300] == "x" * 300
    assert len(long_text[:300]) == 300


def test_wrong_query_dim_raises():
    """vector_search must raise EmbedError when query length != embed_dim."""
    from unittest.mock import MagicMock
    from services.retrieval.embed_client import EmbedError
    from services.retrieval.vector import vector_search

    params = RetrievalParams(embed_model="fake-embed", embed_dim=1024)
    # Pass a 8-dim vector when params says 1024.
    with pytest.raises(EmbedError, match="embed_dim"):
        vector_search(MagicMock(), "some-mailbox-id", [0.1] * 8, params)


def test_hits_sorted_descending_by_vector_score():
    hits = [_make_hit(s) for s in (0.4, 0.9, 0.7, 0.55)]
    hits.sort(key=lambda h: h.vector_score or 0.0, reverse=True)
    scores = [h.vector_score for h in hits]
    assert scores == sorted(scores, reverse=True)


# ── DB-required fixtures ───────────────────────────────────────────────────────

@pytest.fixture()
def session():
    from services.db.engine import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed_mailbox(session, *, n: int = 3) -> tuple[str, list[str]]:
    """Create an isolated mailbox + n messages; embed them; return (mailbox_id, headers).

    Uses _DIM_DB (1024) to satisfy the vector(1024) column constraint from migration 0006.
    """
    from services.db import models as orm
    from scripts.embed_backfill import content_hash, _upsert_batch

    client = _db_client()

    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=f"vtest_{uuid.uuid4().hex[:6]}@example.com",
        status="active",
        embed_model="fake-embed",
        embed_dim=_DIM_DB,
        config={},
    )
    session.add(mbx)
    session.flush()
    mailbox_id = str(mbx.id)

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    thread = orm.Thread(
        mailbox_id=mailbox_id,
        provider_thread_ids=[],
        root_message_id_header="root-vec@x",
        subject_norm="vector test thread",
        participants=["a@test.com", "b@test.com"],
        t_start=t0, t_end=t0, lineage_conflict=False,
    )
    session.add(thread)
    session.flush()
    thread_id = str(thread.id)

    headers: list[str] = []
    items: list[dict] = []

    for i in range(n):
        hdr = f"vec-{i}@example.com"
        msg = orm.Message(
            mailbox_id=mailbox_id,
            message_id_header=hdr,
            provider_id=f"p-vec-{i}",
            thread_id=thread_id,
            sender_email="a@test.com",
            to_emails=["b@test.com"],
            cc_emails=[],
            addresses={},
            ts=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            subject=f"Atlas rollout status update {i}",
            clean_text=f"The atlas deployment is now {i * 10}% complete.",
            link_domains=[],
            sensitivity=["none"],
            noise=False,
        )
        session.add(msg)
        session.flush()
        headers.append(hdr)
        items.append({
            "id":           str(msg.id),
            "subject":      msg.subject,
            "clean_text":   msg.clean_text,
            "content_hash": content_hash(msg.subject, msg.clean_text),
        })

    session.commit()

    texts = [m["subject"] + "\n\n" + m["clean_text"] for m in items]
    embeddings = client.embed_documents(texts)
    _upsert_batch(
        session,
        mailbox_id=mailbox_id,
        model="fake-embed",
        dim=_DIM_DB,
        items=items,
        embeddings=embeddings,
    )
    return mailbox_id, headers


# ── DB integration tests ───────────────────────────────────────────────────────

@_requires_db
def test_vector_search_returns_hits(session):
    from services.retrieval.vector import vector_search

    mailbox_id, _ = _seed_mailbox(session, n=3)
    client = _db_client()
    params = RetrievalParams(embed_model="fake-embed", embed_dim=_DIM_DB, vector_top_k=10)

    hits = vector_search(session, mailbox_id, client.embed_query("atlas rollout"), params)

    assert len(hits) > 0
    assert all(isinstance(h, RetrievalHit) for h in hits)


@_requires_db
def test_vector_search_sorted_descending(session):
    from services.retrieval.vector import vector_search

    mailbox_id, _ = _seed_mailbox(session, n=3)
    client = _db_client()
    params = RetrievalParams(embed_model="fake-embed", embed_dim=_DIM_DB, vector_top_k=10)

    hits = vector_search(session, mailbox_id, client.embed_query("atlas deployment"), params)
    scores = [h.vector_score for h in hits]
    assert scores == sorted(scores, reverse=True)


@_requires_db
def test_vector_search_source_and_fts_fields(session):
    from services.retrieval.vector import vector_search

    mailbox_id, _ = _seed_mailbox(session, n=2)
    client = _db_client()
    params = RetrievalParams(embed_model="fake-embed", embed_dim=_DIM_DB)

    hits = vector_search(session, mailbox_id, client.embed_query("atlas"), params)
    assert all(h.source == "vector" for h in hits)
    assert all(h.fts_score is None for h in hits)


@_requires_db
def test_vector_search_all_headers_in_db(session):
    """Every returned message_id_header must exist in the DB — citation contract."""
    from sqlalchemy import select
    from services.db import models as orm
    from services.retrieval.vector import vector_search

    mailbox_id, _ = _seed_mailbox(session, n=3)
    client = _db_client()
    params = RetrievalParams(embed_model="fake-embed", embed_dim=_DIM_DB, vector_top_k=10)

    hits = vector_search(session, mailbox_id, client.embed_query("atlas"), params)
    for hit in hits:
        row = session.execute(
            select(orm.Message).where(
                orm.Message.mailbox_id == mailbox_id,
                orm.Message.message_id_header == hit.message_id_header,
            )
        ).scalar_one_or_none()
        assert row is not None, f"message_id_header {hit.message_id_header!r} not in DB"


@_requires_db
def test_vector_search_noise_filter(session):
    """Noisy messages must not appear when include_noise=False."""
    from services.db import models as orm
    from services.retrieval.vector import vector_search
    from scripts.embed_backfill import content_hash

    client = _db_client()
    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=f"noise_{uuid.uuid4().hex[:6]}@example.com",
        status="active",
        embed_model="fake-embed",
        embed_dim=_DIM_DB,
        config={},
    )
    session.add(mbx)
    session.flush()
    mailbox_id = str(mbx.id)

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    thread = orm.Thread(
        mailbox_id=mailbox_id, provider_thread_ids=[],
        root_message_id_header="noise-root@x", subject_norm="noise test",
        participants=["a@test.com"], t_start=t0, t_end=t0, lineage_conflict=False,
    )
    session.add(thread)
    session.flush()

    noise_msg = orm.Message(
        mailbox_id=mailbox_id,
        message_id_header="noise@example.com",
        provider_id="p-noise",
        thread_id=str(thread.id),
        sender_email="a@test.com",
        to_emails=[], cc_emails=[], addresses={},
        ts=t0, subject="Unsubscribe", clean_text="You have been unsubscribed.",
        link_domains=[], sensitivity=["none"], noise=True,
    )
    session.add(noise_msg)
    session.commit()

    emb = client.embed_documents([noise_msg.subject + "\n\n" + noise_msg.clean_text])[0]
    emb_row = orm.MessageEmbedding(
        mailbox_id=mailbox_id,
        message_id=str(noise_msg.id),
        embed_model="fake-embed",
        embed_dim=_DIM_DB,
        content_hash=content_hash(noise_msg.subject, noise_msg.clean_text),
        embedded_at=datetime.now(timezone.utc),
        embedding=emb,
    )
    session.add(emb_row)
    session.commit()

    params = RetrievalParams(embed_model="fake-embed", embed_dim=_DIM_DB, include_noise=False)
    hits = vector_search(session, mailbox_id, client.embed_query("unsubscribe"), params)
    assert all(not h.noise for h in hits)


@_requires_db
def test_vector_search_sensitivity_filter(session):
    """Sensitive messages must not appear when include_sensitive=False."""
    from services.db import models as orm
    from services.retrieval.vector import vector_search
    from scripts.embed_backfill import content_hash

    client = _db_client()
    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=f"sens_{uuid.uuid4().hex[:6]}@example.com",
        status="active",
        embed_model="fake-embed",
        embed_dim=_DIM_DB,
        config={},
    )
    session.add(mbx)
    session.flush()
    mailbox_id = str(mbx.id)

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    thread = orm.Thread(
        mailbox_id=mailbox_id, provider_thread_ids=[],
        root_message_id_header="sens-root@x", subject_norm="sensitive test",
        participants=["hr@example.com"], t_start=t0, t_end=t0, lineage_conflict=False,
    )
    session.add(thread)
    session.flush()

    sens_msg = orm.Message(
        mailbox_id=mailbox_id,
        message_id_header="sensitive@example.com",
        provider_id="p-sens",
        thread_id=str(thread.id),
        sender_email="hr@example.com",
        to_emails=[], cc_emails=[], addresses={},
        ts=t0, subject="Salary review",
        clean_text="Your compensation package details.",
        link_domains=[], sensitivity=["hr"], noise=False,
    )
    session.add(sens_msg)
    session.commit()

    emb = client.embed_documents([sens_msg.subject + "\n\n" + sens_msg.clean_text])[0]
    emb_row = orm.MessageEmbedding(
        mailbox_id=mailbox_id,
        message_id=str(sens_msg.id),
        embed_model="fake-embed",
        embed_dim=_DIM_DB,
        content_hash=content_hash(sens_msg.subject, sens_msg.clean_text),
        embedded_at=datetime.now(timezone.utc),
        embedding=emb,
    )
    session.add(emb_row)
    session.commit()

    params = RetrievalParams(
        embed_model="fake-embed", embed_dim=_DIM_DB, include_sensitive=False
    )
    hits = vector_search(session, mailbox_id, client.embed_query("salary"), params)
    assert all("hr" not in h.sensitivity for h in hits)


@_requires_db
def test_vector_search_empty_mailbox_returns_empty(session):
    from services.db import models as orm
    from services.retrieval.vector import vector_search

    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=f"empty_{uuid.uuid4().hex[:6]}@example.com",
        status="active",
        embed_model="fake-embed",
        embed_dim=_DIM_DB,
        config={},
    )
    session.add(mbx)
    session.commit()

    client = _db_client()
    params = RetrievalParams(embed_model="fake-embed", embed_dim=_DIM_DB)
    hits = vector_search(session, str(mbx.id), client.embed_query("anything"), params)
    assert hits == []
