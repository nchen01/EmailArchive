"""S29 admin read service — tenant-scoped queries + allow-list DTO construction.

Every function is scoped to ``tenant_id`` (the caller's principal tenant); rows in
other tenants are simply absent (a detail lookup that resolves to another tenant
returns ``None`` → the router raises 404, no cross-tenant existence oracle, S19 §4).
``full_access`` (True = tenant admin) controls field masking: security reviewers
(``full_access=False``) get domain/masked recipient email and no provider email or
scopes (docs/s28-admin-audit-ops-plan.md §18.3/§18.5).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.db import models as orm

from .contracts import (
    AuditEventView,
    ExclusionSummaryItem,
    ExclusionSummaryView,
    JobAdminView,
    PackageAdminDetail,
    PackageAdminSummary,
    PackageAuditEventView,
    ProviderAccountAdminView,
    ReadinessCheckView,
    ReadinessSummaryView,
    TenantOpsOverview,
)

# Content/secret/error-like metadata keys never surface on the admin audit read,
# even though write-time sanitization already dropped them (defense in depth).
_BLOCKED_KEY_FRAGMENTS = (
    "body", "subject", "snippet", "clean_text", "raw", "mime", "token", "secret",
    "credential", "password", "api_key", "apikey", "exception", "traceback",
    "prompt", "response", "content",
)
_SCALAR = (str, int, float, bool, type(None))


def _is_uuid(value: str | None) -> bool:
    """Guard before any UUID-column query so a malformed id resolves to a safe
    404 (None) instead of raising a DB DataError → 500 (matches the router
    hardening elsewhere). Never lets a driver error surface."""
    if not value:
        return False
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{(local[0] if local else '')}***@{domain}"


def _project_safe_metadata(metadata: dict | None) -> dict:
    if not metadata:
        return {}
    out: dict = {}
    for k, v in metadata.items():
        key = str(k)
        if any(frag in key.lower() for frag in _BLOCKED_KEY_FRAGMENTS):
            continue
        if isinstance(v, _SCALAR):
            out[key] = v
        elif isinstance(v, (list, tuple)) and all(isinstance(i, _SCALAR) for i in v):
            out[key] = list(v)
    return out


def _safe_progress(progress: dict | None) -> dict:
    """Keep numeric counters/flags and a short phase/stage/step string only."""
    out: dict = {}
    for k, v in (progress or {}).items():
        key = str(k)
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, str) and key.lower() in {"phase", "stage", "step"} and len(v) <= 64:
            out[key] = v
    return out


# ── Packages ──────────────────────────────────────────────────────────────────

def _recipient_email(db: Session, package_id: str) -> str | None:
    return db.execute(
        select(orm.HandoffRecipient.recipient_email).where(
            orm.HandoffRecipient.package_id == package_id
        )
    ).scalar_one_or_none()


def _recipient_state(db: Session, package_id: str) -> str | None:
    r = db.execute(
        select(orm.HandoffRecipient).where(orm.HandoffRecipient.package_id == package_id)
    ).scalar_one_or_none()
    if r is None:
        return None
    now = datetime.now(timezone.utc)
    if r.revoked_at is not None:
        return "revoked"
    if r.expires_at is not None and r.expires_at < now:
        return "expired"
    if r.capability_code_consumed_at is not None:
        return "consumed"
    return "granted"


def _exported_at(db: Session, package_id: str) -> str | None:
    ts = db.execute(
        select(func.max(orm.HandoffAuditEvent.ts)).where(
            orm.HandoffAuditEvent.package_id == package_id,
            orm.HandoffAuditEvent.action.ilike("%export%"),
        )
    ).scalar_one_or_none()
    return _iso(ts)


def _summary(pkg: orm.HandoffPackage, recipient_email: str | None, full_access: bool) -> PackageAdminSummary:
    r_email = recipient_email if full_access else _mask_email(recipient_email)
    return PackageAdminSummary(
        id=str(pkg.id), mailbox_id=str(pkg.mailbox_id), title=pkg.title,
        status=pkg.status, version=pkg.version, lineage_id=str(pkg.lineage_id),
        creator_email=pkg.creator_email, reason_category=pkg.reason, recipient_email=r_email,
        created_at=_iso(pkg.created_at), published_at=_iso(pkg.published_at),
        expires_at=_iso(pkg.expires_at), revoked_at=_iso(pkg.revoked_at),
    )


def list_packages(db: Session, *, tenant_id: str, full_access: bool) -> list[PackageAdminSummary]:
    rows = db.execute(
        select(orm.HandoffPackage)
        .join(orm.Mailbox, orm.Mailbox.id == orm.HandoffPackage.mailbox_id)
        .where(orm.Mailbox.tenant_id == tenant_id)
        .order_by(orm.HandoffPackage.created_at.desc())
    ).scalars().all()
    return [_summary(p, _recipient_email(db, str(p.id)), full_access) for p in rows]


def _package_in_tenant(db: Session, *, tenant_id: str, package_id: str) -> orm.HandoffPackage | None:
    if not _is_uuid(package_id):
        return None
    return db.execute(
        select(orm.HandoffPackage)
        .join(orm.Mailbox, orm.Mailbox.id == orm.HandoffPackage.mailbox_id)
        .where(orm.HandoffPackage.id == package_id, orm.Mailbox.tenant_id == tenant_id)
    ).scalar_one_or_none()


def get_package(db: Session, *, tenant_id: str, package_id: str, full_access: bool) -> PackageAdminDetail | None:
    pkg = _package_in_tenant(db, tenant_id=tenant_id, package_id=package_id)
    if pkg is None:
        return None
    base = _summary(pkg, _recipient_email(db, str(pkg.id)), full_access)
    claim_count = db.execute(
        select(func.count()).select_from(orm.HandoffClaim).where(orm.HandoffClaim.package_id == pkg.id)
    ).scalar() or 0
    evidence_count = db.execute(
        select(func.count()).select_from(orm.HandoffEvidence).where(orm.HandoffEvidence.package_id == pkg.id)
    ).scalar() or 0
    return PackageAdminDetail(
        **base.model_dump(),
        policy_mode=pkg.policy_mode,
        supersedes_package_id=str(pkg.supersedes_package_id) if pkg.supersedes_package_id else None,
        exported_at=_exported_at(db, str(pkg.id)),
        recipient_state=_recipient_state(db, str(pkg.id)),
        claim_count=int(claim_count), evidence_count=int(evidence_count),
    )


def get_package_audit(db: Session, *, tenant_id: str, package_id: str) -> list[PackageAuditEventView] | None:
    if _package_in_tenant(db, tenant_id=tenant_id, package_id=package_id) is None:
        return None
    rows = db.execute(
        select(orm.HandoffAuditEvent)
        .where(orm.HandoffAuditEvent.package_id == package_id)
        .order_by(orm.HandoffAuditEvent.ts.asc())
    ).scalars().all()
    return [
        PackageAuditEventView(
            package_id=str(e.package_id),
            lineage_id=str(e.lineage_id) if e.lineage_id else None,
            actor=e.actor, action=e.action, ts=_iso(e.ts),
            safe_metadata=_project_safe_metadata(e.metadata_),
        )
        for e in rows
    ]


# ── Provider accounts ───────────────────────────────────────────────────────

def list_provider_accounts(db: Session, *, tenant_id: str, full_access: bool) -> list[ProviderAccountAdminView]:
    rows = db.execute(
        select(orm.MailboxProviderAccount)
        .where(orm.MailboxProviderAccount.tenant_id == tenant_id)
        .order_by(orm.MailboxProviderAccount.connected_at.desc())
    ).scalars().all()
    # S28 §18.5 (Option A): a security-reviewer-only principal sees provider +
    # connection status + timestamps ONLY. Account/mailbox/owner ids, provider
    # email, scopes, and the mismatch category are omitted (null) — governance
    # posture without provider identity. Tenant admin sees the full metadata.
    return [
        ProviderAccountAdminView(
            id=(str(a.id) if full_access else None),
            mailbox_id=(str(a.mailbox_id) if full_access else None),
            owner_user_id=(str(a.owner_user_id) if full_access else None),
            provider=a.provider,
            provider_account_email=(a.provider_account_email if full_access else None),
            scopes_granted=(list(a.scopes_granted or []) if full_access else []),
            status=a.status,
            connected_at=_iso(a.connected_at), last_verified_at=_iso(a.last_verified_at),
            disconnected_at=_iso(a.disconnected_at),
            mismatch_reason=(a.mismatch_reason if full_access else None),
        )
        for a in rows
    ]


# ── Jobs ────────────────────────────────────────────────────────────────────

def _job_view(j: orm.Job) -> JobAdminView:
    return JobAdminView(
        id=str(j.id), job_type=j.job_type, status=j.status, tenant_id=str(j.tenant_id),
        mailbox_id=str(j.mailbox_id) if j.mailbox_id else None,
        attempt=j.attempt, max_attempts=j.max_attempts,
        created_at=_iso(j.created_at), started_at=_iso(j.started_at),
        finished_at=_iso(j.finished_at), next_retry_at=_iso(j.next_retry_at),
        progress_safe=_safe_progress(j.progress), summary=j.summary,
        error_category=j.error_category,
    )


def list_jobs(db: Session, *, tenant_id: str, limit: int = 100) -> list[JobAdminView]:
    rows = db.execute(
        select(orm.Job).where(orm.Job.tenant_id == tenant_id)
        .order_by(orm.Job.created_at.desc()).limit(limit)
    ).scalars().all()
    return [_job_view(j) for j in rows]


def get_job(db: Session, *, tenant_id: str, job_id: str) -> JobAdminView | None:
    if not _is_uuid(job_id):
        return None
    j = db.get(orm.Job, job_id)
    if j is None or str(j.tenant_id) != tenant_id:
        return None
    return _job_view(j)


# ── Audit log (mailbox/tenant operational audit) ──────────────────────────────

def list_audit(db: Session, *, tenant_id: str, limit: int = 200) -> list[AuditEventView]:
    rows = db.execute(
        select(orm.AuditLog)
        .join(orm.Mailbox, orm.Mailbox.id == orm.AuditLog.mailbox_id)
        .where(orm.Mailbox.tenant_id == tenant_id)
        .order_by(orm.AuditLog.started_at.desc()).limit(limit)
    ).scalars().all()
    return [
        AuditEventView(
            actor=a.actor, action=a.action, scope=a.scope,
            ts=_iso(a.started_at), finished_at=_iso(a.finished_at),
            message_count=a.message_count, mailbox_id=str(a.mailbox_id),
        )
        for a in rows
    ]


# ── Exclusion posture (aggregate counts only) ─────────────────────────────────

def exclusion_summary(db: Session, *, tenant_id: str) -> ExclusionSummaryView:
    rows = db.execute(
        select(
            orm.HandoffExclusion.exclusion_type,
            orm.HandoffExclusion.aggregate_label,
            func.count().label("n"),
        )
        .join(orm.HandoffPackage, orm.HandoffPackage.id == orm.HandoffExclusion.package_id)
        .join(orm.Mailbox, orm.Mailbox.id == orm.HandoffPackage.mailbox_id)
        .where(orm.Mailbox.tenant_id == tenant_id)
        .group_by(orm.HandoffExclusion.exclusion_type, orm.HandoffExclusion.aggregate_label)
    ).all()
    items = [
        ExclusionSummaryItem(exclusion_type=r[0], aggregate_label=r[1], count=int(r[2]))
        for r in rows
    ]
    return ExclusionSummaryView(by_type=items, total_excluded=sum(i.count for i in items))


# ── Readiness (safe messages only; not tenant-scoped) ─────────────────────────

def readiness_summary() -> ReadinessSummaryView:
    from services.hosted_readiness import readiness_failed, run_hosted_checks

    checks = run_hosted_checks()
    return ReadinessSummaryView(
        ready=not readiness_failed(checks),
        checks=[ReadinessCheckView(name=c.name, status=c.status, message=c.message) for c in checks],
    )


# ── Tenant overview ───────────────────────────────────────────────────────────

def overview(db: Session, *, tenant_id: str) -> TenantOpsOverview:
    pkg_rows = db.execute(
        select(orm.HandoffPackage.status, func.count())
        .join(orm.Mailbox, orm.Mailbox.id == orm.HandoffPackage.mailbox_id)
        .where(orm.Mailbox.tenant_id == tenant_id)
        .group_by(orm.HandoffPackage.status)
    ).all()
    job_rows = db.execute(
        select(orm.Job.status, func.count())
        .where(orm.Job.tenant_id == tenant_id)
        .group_by(orm.Job.status)
    ).all()
    active_accounts = db.execute(
        select(func.count()).select_from(orm.MailboxProviderAccount)
        .where(orm.MailboxProviderAccount.tenant_id == tenant_id,
               orm.MailboxProviderAccount.status == "connected")
    ).scalar() or 0

    from services.hosted_readiness import evaluate_readiness
    ready, _ = evaluate_readiness()

    return TenantOpsOverview(
        package_counts_by_status={r[0]: int(r[1]) for r in pkg_rows},
        active_provider_accounts=int(active_accounts),
        job_counts_by_status={r[0]: int(r[1]) for r in job_rows},
        degraded_readiness=not ready,
    )
