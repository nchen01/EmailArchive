"""SQLAlchemy 2.0 ORM models — one class per physical table (spec 04 §3-6).

These mirror the authoritative SQL in spec 04. Shapes (the Pydantic contract)
live in ``packages/ekc_schemas/models.py``; this module only describes *storage*.

Notes
-----
- ``message.clean_text_tsv`` and ``message.subject_clean_tsv`` are Postgres
  GENERATED columns created in migrations via ``op.execute`` (autogenerate can't
  express them). They are not mapped — the Python layer queries them via raw SQL.
- ``MessageEmbedding`` uses ``pgvector.sqlalchemy.Vector`` (migration 0006, D12b).
  Requires ``pip install pgvector``.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
    # S22 auth/tenant boundary (docs/s19-auth-tenant-boundary-plan.md §3). Nullable
    # so existing ingest/seed inserts keep working; the 0010 migration backfills
    # existing rows to the local dev tenant/user. Production authorization treats a
    # NULL owner as unowned (fail closed); dev mode owns local/demo mailboxes.
    tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    owner_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
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


# ── Auth / tenant boundary (S22 — implements docs/s19-auth-tenant-boundary-plan) ─
# Service-DB only (no ekc_schemas shared-contract change). A Tenant is the
# isolation boundary; a User is an authenticated identity within one tenant; a
# TenantMembership grants a role. A Mailbox is owned by one User in one Tenant
# (see Mailbox.tenant_id / owner_user_id above). Recipients are NOT modeled here —
# they authenticate to a package via a capability code + session (S19 §5).
class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AppUser(Base):
    # 'user' is reserved in Postgres, so the table is 'app_user'.
    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    idp_subject: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "idp_subject", name="uq_app_user_tenant_subject"),
    )


class TenantMembership(Base):
    __tablename__ = "tenant_membership"

    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    granted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('creator','admin','security_reviewer')",
            name="ck_tenant_membership_role",
        ),
    )


# ── Gmail OAuth + token vault (S23 — implements docs/s20-oauth-token-vault-plan) ─
# Service-DB only (no ekc_schemas change, SCHEMA_VERSION not bumped). The app DB
# stores only a vault_ref + SAFE provider metadata — never raw access/refresh
# tokens (those live only in the token vault, services/oauth/vault.py).
class MailboxProviderAccount(Base):
    __tablename__ = "mailbox_provider_account"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    mailbox_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="gmail")
    provider_account_email: Mapped[str] = mapped_column(Text, nullable=False)
    provider_account_sub: Mapped[str | None] = mapped_column(Text)
    # Opaque handle into the token vault — NOT a token.
    vault_ref: Mapped[str | None] = mapped_column(Text)
    scopes_granted: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="connected")
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Safe diagnostics for a rejected connect (never raw provider data).
    expected_account_email: Mapped[str | None] = mapped_column(Text)
    mismatch_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "mailbox_id", "provider", name="uq_provider_account_mailbox"
        ),
        CheckConstraint("provider IN ('gmail')", name="ck_provider_account_provider"),
        CheckConstraint(
            "status IN ('connected','refresh_failed','revoked','disconnected','mismatch_blocked')",
            name="ck_provider_account_status",
        ),
    )


class OAuthState(Base):
    # Single-use CSRF/PKCE state, bound to {tenant,user,mailbox,provider}. Holds
    # the PKCE code_verifier (an ephemeral per-flow secret, NOT an OAuth token).
    __tablename__ = "oauth_state"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # the opaque `state` value
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    mailbox_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="gmail")
    code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    # No ForeignKey intentional: audit rows survive mailbox deletion (spec 04, append-only retention).
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


# ── L2 — embeddings (spec 04 §6, migration 0006, D12b) ───────────────────────

class MessageEmbedding(Base):
    __tablename__ = "message_embedding"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("message.id", ondelete="CASCADE"), nullable=False
    )
    embed_model: Mapped[str] = mapped_column(Text, nullable=False)
    embed_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Vector(1024) is locked to voyage-4 for S7 (D12b). Future model changes
    # require a new migration and backfill.
    embedding: Mapped[Any] = mapped_column(Vector(1024), nullable=False)

    __table_args__ = (
        UniqueConstraint("message_id", "embed_model", name="uq_message_embedding_msg_model"),
        CheckConstraint("embed_dim > 0", name="ck_message_embedding_dim"),
    )


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


# ── S17 — handoff package (product/API layer, NOT ekc_schemas; migration 0007) ─
#
# Draft/generate slice only: publish, recipient, and session-token tables are
# deferred to the publish/recipient-view work. See docs/s17.2-handoff-package-
# domain-spec.md.

class HandoffPackage(Base):
    __tablename__ = "handoff_package"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    mailbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("mailbox.id", ondelete="CASCADE"), nullable=False
    )
    creator_email: Mapped[str] = mapped_column(Text, nullable=False)
    creator_person_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("person.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    policy_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="standard")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    supersedes_package_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("handoff_package.id", ondelete="SET NULL")
    )
    lineage_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HandoffScope(Base):
    __tablename__ = "handoff_scope"

    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("handoff_package.id", ondelete="CASCADE"),
        primary_key=True,
    )
    date_from: Mapped[date | None] = mapped_column(Date)
    date_to: Mapped[date | None] = mapped_column(Date)
    included_project_ids: Mapped[list[str]] = mapped_column(
        ARRAY(UUID(as_uuid=False)), nullable=False, server_default="{}"
    )
    included_person_ids: Mapped[list[str]] = mapped_column(
        ARRAY(UUID(as_uuid=False)), nullable=False, server_default="{}"
    )
    included_thread_ids: Mapped[list[str]] = mapped_column(
        ARRAY(UUID(as_uuid=False)), nullable=False, server_default="{}"
    )
    excluded_thread_ids: Mapped[list[str]] = mapped_column(
        ARRAY(UUID(as_uuid=False)), nullable=False, server_default="{}"
    )
    excluded_message_id_headers: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    allowed_domains: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    keyword_filters: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )


class HandoffClaim(Base):
    __tablename__ = "handoff_claim"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("handoff_package.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    source_message_id_headers: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="0")


class HandoffEvidence(Base):
    __tablename__ = "handoff_evidence"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("handoff_package.id", ondelete="CASCADE"), nullable=False
    )
    message_id_header: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    sender_display: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    sender_domain: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    body_snapshot: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_type: Mapped[str | None] = mapped_column(Text)
    snapshotted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("package_id", "message_id_header", name="uq_handoff_evidence_pkg_header"),
    )


class HandoffExclusion(Base):
    __tablename__ = "handoff_exclusion"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("handoff_package.id", ondelete="CASCADE"), nullable=False
    )
    exclusion_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_ref: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    aggregate_label: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    audit_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class HandoffAuditEvent(Base):
    __tablename__ = "handoff_audit_event"

    # No FK to handoff_package: audit rows are retained after package deletion.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    package_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    lineage_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")


# ── S17.5 — publish / recipient foundation (migration 0008) ───────────────────
#
# The recipient authenticates with a one-time capability CODE; only its sha256
# hash is stored. Session exchange issues a short-lived bearer token whose hash
# alone is stored. Raw tokens are never persisted (a leaked row cannot be
# replayed). Exactly one recipient per package (unique package_id).

class HandoffRecipient(Base):
    __tablename__ = "handoff_recipient"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("handoff_package.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    recipient_email: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_person_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("person.id", ondelete="SET NULL")
    )
    capability_code_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # One-time code: set once, atomically, on the first successful session exchange
    # (S17.2 §7.1). NULL means not yet consumed; once set the code is spent and any
    # replay returns the neutral "unavailable" response.
    capability_code_consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HandoffRecipientSession(Base):
    __tablename__ = "handoff_recipient_session"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("handoff_package.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("handoff_recipient.id", ondelete="CASCADE"), nullable=False
    )
    session_token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
