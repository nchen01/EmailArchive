"""SQLAlchemy 2.0 ORM models — one class per physical table (spec 04 §3-5).

These mirror the authoritative SQL in spec 04. Shapes (the Pydantic contract)
live in ``packages/ekc_schemas/models.py``; this module only describes *storage*.

Notes
-----
- ``message.clean_text_tsv`` is a Postgres GENERATED column and is created in the
  Alembic migration via ``op.execute`` (autogenerate can't express it). It is not
  mapped here — the Python layer never reads it (FTS-only).
- ``message_embedding`` (pgvector HNSW) is deferred — see TODO below.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ── Tenant root & operational tables (spec 04 §3) ────────────────────────────

class Mailbox(Base):
    __tablename__ = "mailbox"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    owner_email: Mapped[str] = mapped_column(Text, nullable=False)
    owner_person_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    embed_model: Mapped[str] = mapped_column(Text, nullable=False)
    embed_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    display_threshold: Mapped[float] = mapped_column(
        Numeric, nullable=False, server_default="0.4"
    )
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("provider IN ('gmail','msgraph')", name="ck_mailbox_provider"),
    )


class SyncState(Base):
    __tablename__ = "sync_state"

    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("mailbox.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sync_token: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mailbox_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str | None] = mapped_column(Text)
    message_count: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_token: Mapped[str | None] = mapped_column(Text)


class ProjectLabelOverride(Base):
    __tablename__ = "project_label_override"

    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    cluster_signature: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        PrimaryKeyConstraint("mailbox_id", "cluster_signature"),
    )


class SchemaMeta(Base):
    __tablename__ = "schema_meta"

    k: Mapped[str] = mapped_column(Text, primary_key=True)
    v: Mapped[str] = mapped_column(Text, nullable=False)


# ── L0 — messages & threads (spec 04 §4) ─────────────────────────────────────

class Thread(Base):
    __tablename__ = "thread"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    provider_thread_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    root_message_id_header: Mapped[str | None] = mapped_column(Text)
    subject_norm: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    participants: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    t_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    t_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lineage_conflict: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Message(Base):
    __tablename__ = "message"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    message_id_header: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("thread.id", ondelete="CASCADE"), nullable=False
    )
    sender_email: Mapped[str] = mapped_column(Text, nullable=False)
    to_emails: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    cc_emails: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    addresses: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    clean_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # clean_text_tsv: GENERATED column, created in migration, not mapped (FTS-only).
    link_domains: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    sensitivity: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{none}"
    )
    noise: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    raw_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("mailbox_id", "message_id_header", name="uq_message_mbx_header"),
    )


class MessageAttachment(Base):
    __tablename__ = "message_attachment"

    message_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("message.id", ondelete="CASCADE"), nullable=False
    )
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    mimetype: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("message_id", "sha256"),
    )


# TODO: ticket 4.5 — message_embedding table (pgvector HNSW, vector(D)).
# Deferred: D depends on the embedding model, which isn't chosen yet.


# ── L1 — people, graph (spec 04 §5) ──────────────────────────────────────────

class Org(Base):
    __tablename__ = "org"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domains: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class Person(Base):
    __tablename__ = "person"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    canonical_email: Mapped[str] = mapped_column(Text, nullable=False)
    names: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    org_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("org.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    role_confidence: Mapped[float] = mapped_column(
        Numeric, nullable=False, server_default="0.0"
    )

    __table_args__ = (
        UniqueConstraint("mailbox_id", "canonical_email", name="uq_person_mbx_email"),
        CheckConstraint(
            "role_confidence BETWEEN 0 AND 1", name="ck_person_role_confidence"
        ),
    )


class Identity(Base):
    __tablename__ = "identity"

    # NOTE: mailbox_id is TEXT here (not a UUID FK) — spec 04 §5.
    mailbox_id: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_names: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    person_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("person.id", ondelete="CASCADE")
    )

    __table_args__ = (
        PrimaryKeyConstraint("mailbox_id", "email"),
    )


class Edge(Base):
    __tablename__ = "edge"

    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_to_count: Mapped[int] = mapped_column(Integer, nullable=False)
    received_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_contact: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_contact: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    weight: Mapped[float] = mapped_column(Numeric, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("mailbox_id", "person_id"),
        CheckConstraint("message_count >= 0", name="ck_edge_msg_count"),
        CheckConstraint("sent_to_count >= 0", name="ck_edge_sent_count"),
        CheckConstraint("received_count >= 0", name="ck_edge_recv_count"),
        CheckConstraint("weight >= 0", name="ck_edge_weight"),
    )


# ── L1 — projects & events (spec 04 §5) ──────────────────────────────────────

class Project(Base):
    __tablename__ = "project"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    label_source: Mapped[str] = mapped_column(Text, nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(
        "end", DateTime(timezone=True), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Numeric, nullable=False)
    debug: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_project_confidence"),
    )


class ProjectMember(Base):
    __tablename__ = "project_member"

    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    involvement: Mapped[float] = mapped_column(Numeric, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("project_id", "person_id"),
        CheckConstraint("involvement >= 0", name="ck_pm_involvement"),
        CheckConstraint("message_count >= 0", name="ck_pm_msg_count"),
    )


class ThreadProjectAssignment(Base):
    __tablename__ = "thread_project_assignment"

    thread_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("thread.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    weight: Mapped[float] = mapped_column(Numeric, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("thread_id", "project_id"),
        CheckConstraint("weight > 0 AND weight <= 1", name="ck_tpa_weight"),
    )


class Event(Base):
    __tablename__ = "event"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    actor_person_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("project.id", ondelete="SET NULL")
    )
    source_message_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric, nullable=False, server_default="0.0"
    )

    __table_args__ = (
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_event_confidence"),
        CheckConstraint(
            "cardinality(source_message_ids) >= 1", name="ck_event_has_source"
        ),
    )
