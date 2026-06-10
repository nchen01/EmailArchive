"""S7.1/S7.2 tests — migration 0006 and MessageEmbedding ORM/schema round-trips.

Offline tests (no DB required) verify imports and Pydantic validation.
DB tests (skip if Postgres unreachable) verify the migration applied correctly
and that the ORM can insert and retrieve a MessageEmbedding row.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────

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

_FAKE_DIM = 1024
_FAKE_EMBEDDING = [0.1 / _FAKE_DIM] * _FAKE_DIM  # unit-ish vector


def _content_hash(subject: str, clean_text: str) -> str:
    blob = f"{subject}\n\n{clean_text}"
    return hashlib.sha256(blob.encode()).hexdigest()


# ── offline: schema model ─────────────────────────────────────────────────────

def test_message_embedding_record_public_import():
    """MessageEmbeddingRecord must be reachable via the canonical public path."""
    from ekc_schemas import MessageEmbeddingRecord  # not ekc_schemas.models
    assert MessageEmbeddingRecord is not None


def test_message_embedding_record_importable():
    from ekc_schemas.models import MessageEmbeddingRecord
    assert MessageEmbeddingRecord is not None


def test_message_embedding_record_valid():
    from ekc_schemas.models import MessageEmbeddingRecord
    rec = MessageEmbeddingRecord(
        message_id=str(uuid.uuid4()),
        mailbox_id=str(uuid.uuid4()),
        embed_model="voyage-4",
        embed_dim=1024,
        content_hash=_content_hash("Hello", "Body text here."),
        embedded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        embedding=_FAKE_EMBEDDING,
    )
    assert rec.embed_dim == 1024
    assert len(rec.embedding) == 1024
    assert rec.embed_model == "voyage-4"


def test_message_embedding_record_rejects_zero_dim():
    from ekc_schemas.models import MessageEmbeddingRecord
    with pytest.raises(Exception):
        MessageEmbeddingRecord(
            message_id=str(uuid.uuid4()),
            mailbox_id=str(uuid.uuid4()),
            embed_model="voyage-4",
            embed_dim=0,
            content_hash="abc",
            embedded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
            embedding=[],
        )


def test_message_embedding_record_rejects_length_mismatch():
    """embed_dim=1024 with a 3-element vector must fail validation."""
    from ekc_schemas.models import MessageEmbeddingRecord
    with pytest.raises(Exception, match="embed_dim"):
        MessageEmbeddingRecord(
            message_id=str(uuid.uuid4()),
            mailbox_id=str(uuid.uuid4()),
            embed_model="voyage-4",
            embed_dim=1024,
            content_hash="abc",
            embedded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
            embedding=[0.1, 0.2, 0.3],  # wrong length
        )


# ── offline: mappers ─────────────────────────────────────────────────────────

def test_embedding_to_row_shape():
    """embedding_to_row returns the expected column dict without an id key."""
    from ekc_schemas import MessageEmbeddingRecord
    from services.db.mappers import embedding_to_row

    mid = str(uuid.uuid4())
    mbx = str(uuid.uuid4())
    rec = MessageEmbeddingRecord(
        message_id=mid,
        mailbox_id=mbx,
        embed_model="voyage-4",
        embed_dim=4,
        content_hash="deadbeef",
        embedded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        embedding=[0.1, 0.2, 0.3, 0.4],
    )
    row = embedding_to_row(rec)

    assert "id" not in row, "id must be DB-generated, not supplied by the mapper"
    assert row["message_id"] == mid
    assert row["mailbox_id"] == mbx
    assert row["embed_model"] == "voyage-4"
    assert row["embed_dim"] == 4
    assert row["content_hash"] == "deadbeef"
    assert row["embedding"] == [0.1, 0.2, 0.3, 0.4]


def test_row_to_embedding_round_trip():
    """row_to_embedding reconstructs a valid MessageEmbeddingRecord from a mock row."""
    from types import SimpleNamespace
    from services.db.mappers import row_to_embedding

    mid = str(uuid.uuid4())
    mbx = str(uuid.uuid4())
    fake_row = SimpleNamespace(
        message_id=mid,
        mailbox_id=mbx,
        embed_model="voyage-4",
        embed_dim=4,
        content_hash="deadbeef",
        embedded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        embedding=[0.1, 0.2, 0.3, 0.4],
    )
    rec = row_to_embedding(fake_row)

    assert rec.message_id == mid
    assert rec.mailbox_id == mbx
    assert rec.embed_model == "voyage-4"
    assert rec.embed_dim == 4
    assert rec.embedding == [0.1, 0.2, 0.3, 0.4]


def test_row_to_embedding_enforces_length_validator():
    """row_to_embedding must raise if DB vector length does not match embed_dim."""
    from types import SimpleNamespace
    from services.db.mappers import row_to_embedding

    fake_row = SimpleNamespace(
        message_id=str(uuid.uuid4()),
        mailbox_id=str(uuid.uuid4()),
        embed_model="voyage-4",
        embed_dim=1024,          # claims 1024 ...
        content_hash="x",
        embedded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        embedding=[0.1, 0.2],   # ... but only 2 elements
    )
    with pytest.raises(Exception, match="embed_dim"):
        row_to_embedding(fake_row)


# ── offline: ORM model ────────────────────────────────────────────────────────

def test_message_embedding_orm_importable():
    from services.db.models import MessageEmbedding
    assert MessageEmbedding.__tablename__ == "message_embedding"


def test_message_embedding_orm_has_expected_columns():
    from services.db.models import MessageEmbedding
    cols = {c.name for c in MessageEmbedding.__table__.columns}
    assert cols >= {
        "id", "mailbox_id", "message_id",
        "embed_model", "embed_dim", "content_hash",
        "embedded_at", "embedding",
    }


def test_message_embedding_orm_unique_constraint_defined():
    from services.db.models import MessageEmbedding
    constraint_names = {c.name for c in MessageEmbedding.__table__.constraints}
    assert "uq_message_embedding_msg_model" in constraint_names


# ── DB: table structure ───────────────────────────────────────────────────────

@_requires_db
def test_subject_clean_tsv_column_exists():
    """Migration 0006 must add subject_clean_tsv to the message table."""
    from sqlalchemy import text
    from services.db.engine import SessionLocal
    with SessionLocal() as session:
        result = session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'message' AND column_name = 'subject_clean_tsv'"
            )
        ).fetchone()
    assert result is not None, "subject_clean_tsv column not found on message table"


@_requires_db
def test_message_embedding_table_exists():
    from sqlalchemy import text
    from services.db.engine import SessionLocal
    with SessionLocal() as session:
        result = session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name = 'message_embedding'"
            )
        ).fetchone()
    assert result is not None, "message_embedding table not found"


@_requires_db
def test_schema_meta_version_after_upgrade():
    """After alembic upgrade head, schema_meta.SCHEMA_VERSION must be 0.2.0."""
    from sqlalchemy import text
    from ekc_schemas import SCHEMA_VERSION
    from services.db.engine import SessionLocal
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT v FROM schema_meta WHERE k = 'SCHEMA_VERSION'")
        ).fetchone()
    assert row is not None, "schema_meta row for SCHEMA_VERSION not found"
    assert row[0] == SCHEMA_VERSION, (
        f"schema_meta.SCHEMA_VERSION is {row[0]!r}, expected {SCHEMA_VERSION!r}. "
        "Run alembic upgrade head to apply migration 0006."
    )


@_requires_db
def test_message_embedding_hnsw_index_exists():
    from sqlalchemy import text
    from services.db.engine import SessionLocal
    with SessionLocal() as session:
        result = session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'message_embedding' "
                "AND indexname = 'ix_message_embedding_hnsw'"
            )
        ).fetchone()
    assert result is not None, "HNSW index ix_message_embedding_hnsw not found"


# ── DB: ORM round-trip ────────────────────────────────────────────────────────

@pytest.fixture()
def _session():
    from services.db.engine import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def _mailbox_and_message(_session):
    """Insert a minimal mailbox + message row; cascade-delete on teardown."""
    from services.db import models as orm

    mbx = orm.Mailbox(
        provider="gmail",
        owner_email="test@example.com",
        embed_model="voyage-4",
        embed_dim=1024,
        config={},
    )
    _session.add(mbx)
    _session.flush()

    thread = orm.Thread(
        mailbox_id=mbx.id,
        t_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        t_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _session.add(thread)
    _session.flush()

    msg = orm.Message(
        mailbox_id=mbx.id,
        message_id_header=f"<test-{uuid.uuid4()}@example.com>",
        provider_id=str(uuid.uuid4()),
        thread_id=thread.id,
        sender_email="sender@example.com",
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        subject="Test subject",
        clean_text="Test body content.",
    )
    _session.add(msg)
    _session.flush()
    return mbx, msg


@_requires_db
def test_message_embedding_insert_and_retrieve(_session, _mailbox_and_message):
    from services.db import models as orm

    mbx, msg = _mailbox_and_message
    emb = orm.MessageEmbedding(
        mailbox_id=mbx.id,
        message_id=msg.id,
        embed_model="voyage-4",
        embed_dim=1024,
        content_hash=_content_hash(msg.subject, msg.clean_text),
        embedded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        embedding=_FAKE_EMBEDDING,
    )
    _session.add(emb)
    _session.flush()

    from sqlalchemy import select
    row = _session.execute(
        select(orm.MessageEmbedding).where(orm.MessageEmbedding.id == emb.id)
    ).scalar_one()

    assert row.embed_model == "voyage-4"
    assert row.embed_dim == 1024
    assert len(row.embedding) == 1024
    assert row.message_id == msg.id


@_requires_db
def test_message_embedding_unique_constraint(_session, _mailbox_and_message):
    """Inserting two rows with the same (message_id, embed_model) must fail."""
    from sqlalchemy.exc import IntegrityError
    from services.db import models as orm

    mbx, msg = _mailbox_and_message
    for _ in range(2):
        _session.add(orm.MessageEmbedding(
            mailbox_id=mbx.id,
            message_id=msg.id,
            embed_model="voyage-4",
            embed_dim=1024,
            content_hash="hash-abc",
            embedded_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
            embedding=_FAKE_EMBEDDING,
        ))

    with pytest.raises(IntegrityError):
        _session.flush()
