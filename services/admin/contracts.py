"""S29 admin read-model DTOs (allow-list). Each model names ONLY safe metadata
fields (docs/s28-admin-audit-ops-plan.md §4). Forbidden fields — evidence
bodies, claim text, scope detail, source headers, vault_ref, tokens, codes,
raw job params/errors, sync tokens, tracebacks — are deliberately absent."""
from __future__ import annotations

from pydantic import BaseModel


class PackageAdminSummary(BaseModel):
    id: str
    mailbox_id: str
    title: str
    status: str
    version: int
    lineage_id: str
    creator_email: str
    # `reason` is a constrained safe enum (vacation/leave/transfer/delegation/other),
    # not free text, so it is exposed as a structured category (S28 §18.2). It never
    # carries creator free-text or content.
    reason_category: str
    recipient_email: str | None  # full for admin; domain/masked for security reviewer
    created_at: str | None
    published_at: str | None
    expires_at: str | None
    revoked_at: str | None


class PackageAdminDetail(PackageAdminSummary):
    policy_mode: str
    supersedes_package_id: str | None
    exported_at: str | None
    recipient_state: str | None  # granted/consumed/expired/revoked — derived, no hashes
    claim_count: int
    evidence_count: int


class PackageAuditEventView(BaseModel):
    package_id: str
    lineage_id: str | None
    actor: str
    action: str
    ts: str | None
    safe_metadata: dict  # whitelisted projection only


class ProviderAccountAdminView(BaseModel):
    # For a security-reviewer-only principal, identity fields are null: only
    # provider + status + timestamps are returned (S28 §18.5, Option A).
    id: str | None
    mailbox_id: str | None
    owner_user_id: str | None
    provider: str
    provider_account_email: str | None  # None for security reviewer
    scopes_granted: list[str]           # [] for security reviewer
    status: str
    connected_at: str | None
    last_verified_at: str | None
    disconnected_at: str | None
    mismatch_reason: str | None         # None for security reviewer


class JobAdminView(BaseModel):
    id: str
    job_type: str
    status: str
    tenant_id: str
    mailbox_id: str | None
    attempt: int
    max_attempts: int
    created_at: str | None
    started_at: str | None
    finished_at: str | None
    next_retry_at: str | None
    progress_safe: dict
    summary: str | None
    error_category: str | None
    # NB: NO params, error_message, idempotency_key, or worker_id (S28 §10).


class AuditEventView(BaseModel):
    actor: str
    action: str
    scope: str | None
    ts: str | None
    finished_at: str | None
    message_count: int | None
    mailbox_id: str
    # NB: NO sync_token (S28 §3).


class ExclusionSummaryItem(BaseModel):
    exclusion_type: str
    aggregate_label: str
    count: int


class ExclusionSummaryView(BaseModel):
    by_type: list[ExclusionSummaryItem]
    total_excluded: int


class ReadinessCheckView(BaseModel):
    name: str
    status: str
    message: str


class ReadinessSummaryView(BaseModel):
    ready: bool
    checks: list[ReadinessCheckView]


class TenantOpsOverview(BaseModel):
    package_counts_by_status: dict
    active_provider_accounts: int
    job_counts_by_status: dict
    degraded_readiness: bool
