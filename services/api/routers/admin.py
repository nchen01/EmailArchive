"""S29 — read-only Admin / Audit Viewer + Operations metadata
(implements docs/s28-admin-audit-ops-plan.md, read set of §5).

All routes are under /api/admin/*, tenant-scoped by the caller's principal, and
guarded by the S22 governance role dependencies (require_admin /
require_admin_or_reviewer). They return SAFE METADATA ONLY — never mailbox/package
content, tokens, vault refs, raw job params, DB URLs, or tracebacks (§8/§9). There
are NO mutation routes in S29 (revoke/disconnect are S30). Recipients have no
principal and cannot reach these routes; the recipient package surface is
unchanged and package-local snapshot-only.

Masking: an `admin` sees full metadata; a `security_reviewer` (without admin) sees
a domain/masked recipient email and no provider email/scopes (§18.3/§18.5).
Cross-tenant lookups return 404 (no existence oracle, S19 §4).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.admin import contracts, read_service
from services.db import models as orm

from ..auth import Principal, require_admin, require_admin_or_reviewer
from ..deps import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


class ReasonRequest(BaseModel):
    """Mandatory governance reason for an admin action (S30). Bounded; treated as
    safe governance metadata, never mailbox content."""
    reason: str = Field(..., max_length=500)


class _NullVault:
    """Fallback so an admin disconnect still marks the DB row + purges the ref even
    when no vault is available. Never exposes a token."""

    def revoke(self, vault_ref: str) -> None:  # pragma: no cover - trivial
        return None


def _vault_or_null():
    from services.oauth.vault import VaultError, get_vault

    try:
        return get_vault()
    except VaultError:
        return _NullVault()


def _full_access(principal: Principal) -> bool:
    """Admin sees full metadata; a security-reviewer-only principal is masked."""
    return "admin" in principal.roles


def _audit_sensitive_read(db: Session, *, package_id: str, lineage_id: str | None, principal: Principal) -> None:
    """Best-effort audit of a sensitive package detail read (§18.4). Package-scoped
    sink only; safe metadata (role + actor id), never content. Never fails the read."""
    try:
        from services.handoff.audit import write_handoff_audit

        role = "admin" if _full_access(principal) else "security_reviewer"
        write_handoff_audit(
            db, package_id=package_id, lineage_id=lineage_id,
            actor=f"{role}:{principal.user_id}", action="admin.package.viewed",
            metadata={"role": role},
        )
    except Exception:
        db.rollback()


@router.get("/overview", response_model=contracts.TenantOpsOverview)
async def overview(
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> contracts.TenantOpsOverview:
    return read_service.overview(db, tenant_id=principal.tenant_id)


@router.get("/packages", response_model=list[contracts.PackageAdminSummary])
async def list_packages(
    principal: Principal = Depends(require_admin_or_reviewer),
    db: Session = Depends(get_db),
) -> list[contracts.PackageAdminSummary]:
    return read_service.list_packages(db, tenant_id=principal.tenant_id, full_access=_full_access(principal))


@router.get("/packages/{package_id}", response_model=contracts.PackageAdminDetail)
async def get_package(
    package_id: str,
    principal: Principal = Depends(require_admin_or_reviewer),
    db: Session = Depends(get_db),
) -> contracts.PackageAdminDetail:
    detail = read_service.get_package(
        db, tenant_id=principal.tenant_id, package_id=package_id, full_access=_full_access(principal)
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Not found.")
    _audit_sensitive_read(db, package_id=detail.id, lineage_id=detail.lineage_id, principal=principal)
    return detail


@router.get("/packages/{package_id}/audit", response_model=list[contracts.PackageAuditEventView])
async def get_package_audit(
    package_id: str,
    principal: Principal = Depends(require_admin_or_reviewer),
    db: Session = Depends(get_db),
) -> list[contracts.PackageAuditEventView]:
    events = read_service.get_package_audit(db, tenant_id=principal.tenant_id, package_id=package_id)
    if events is None:
        raise HTTPException(status_code=404, detail="Not found.")
    _audit_sensitive_read(
        db, package_id=package_id,
        lineage_id=(events[0].lineage_id if events else None), principal=principal,
    )
    return events


@router.get("/provider-accounts", response_model=list[contracts.ProviderAccountAdminView])
async def list_provider_accounts(
    principal: Principal = Depends(require_admin_or_reviewer),
    db: Session = Depends(get_db),
) -> list[contracts.ProviderAccountAdminView]:
    return read_service.list_provider_accounts(
        db, tenant_id=principal.tenant_id, full_access=_full_access(principal)
    )


@router.get("/jobs", response_model=list[contracts.JobAdminView])
async def list_jobs(
    principal: Principal = Depends(require_admin_or_reviewer),
    db: Session = Depends(get_db),
) -> list[contracts.JobAdminView]:
    return read_service.list_jobs(db, tenant_id=principal.tenant_id)


@router.get("/jobs/{job_id}", response_model=contracts.JobAdminView)
async def get_job(
    job_id: str,
    principal: Principal = Depends(require_admin_or_reviewer),
    db: Session = Depends(get_db),
) -> contracts.JobAdminView:
    job = read_service.get_job(db, tenant_id=principal.tenant_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return job


@router.get("/audit", response_model=list[contracts.AuditEventView])
async def list_audit(
    principal: Principal = Depends(require_admin_or_reviewer),
    db: Session = Depends(get_db),
) -> list[contracts.AuditEventView]:
    return read_service.list_audit(db, tenant_id=principal.tenant_id)


@router.get("/exclusions/summary", response_model=contracts.ExclusionSummaryView)
async def exclusion_summary(
    principal: Principal = Depends(require_admin_or_reviewer),
    db: Session = Depends(get_db),
) -> contracts.ExclusionSummaryView:
    return read_service.exclusion_summary(db, tenant_id=principal.tenant_id)


@router.get("/readiness", response_model=contracts.ReadinessSummaryView)
async def readiness(
    principal: Principal = Depends(require_admin),
) -> contracts.ReadinessSummaryView:
    return read_service.readiness_summary()


# ── Admin actions (S30) — tenant-admin only, mandatory reason, audited ─────────

@router.post("/packages/{package_id}/revoke", response_model=contracts.PackageAdminDetail)
async def revoke_package(
    package_id: str,
    body: ReasonRequest,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> contracts.PackageAdminDetail:
    """Governance revoke of a published package (S28 §7). Same lifecycle semantics
    as creator revoke — blocks recipient access + kills live sessions — plus an
    `package.revoked_by_admin` audit event carrying the mandatory reason. Metadata
    only; never touches package content."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")
    pkg = read_service.resolve_package(db, tenant_id=principal.tenant_id, package_id=package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="Not found.")

    if pkg.status == "published":
        from services.handoff.lifecycle import revoke_package as _revoke
        _revoke(
            db, pkg, actor=f"admin:{principal.user_id}", action="package.revoked_by_admin",
            extra_metadata={
                "reason": reason, "admin_user_id": principal.user_id, "prior_status": "published",
            },
        )
    elif pkg.status != "revoked":
        # draft / superseded → not revokable (matches creator lifecycle). Already
        # 'revoked' is an idempotent no-op success (no duplicate audit).
        raise HTTPException(
            status_code=409,
            detail=f"only a published package can be revoked (status '{pkg.status}')",
        )

    detail = read_service.get_package(
        db, tenant_id=principal.tenant_id, package_id=package_id, full_access=True
    )
    if detail is None:  # pragma: no cover - resolved above
        raise HTTPException(status_code=404, detail="Not found.")
    return detail


@router.post("/provider-accounts/{account_id}/disconnect", response_model=contracts.ProviderAccountAdminView)
async def disconnect_provider_account(
    account_id: str,
    body: ReasonRequest,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> contracts.ProviderAccountAdminView:
    """Governance disconnect of a provider account (S28 §7): provider-side revoke +
    vault purge, mark disconnected, and an audit event carrying the mandatory reason.
    Never exposes a token/vault_ref; no silent reconnect, no connect-on-behalf."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")
    acct = read_service.resolve_provider_account(db, tenant_id=principal.tenant_id, account_id=account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Not found.")

    from services.oauth import flow

    did_disconnect = flow.disconnect_account(db, account=acct, vault=_vault_or_null())
    if did_disconnect:
        # Safe audit: actor=admin, action, mailbox_id, timestamp, reason (in scope).
        # provider is implicit (gmail). No token/vault_ref/provider response recorded.
        db.add(orm.AuditLog(
            mailbox_id=str(acct.mailbox_id), actor=principal.user_id,
            action="provider_account_disconnected_by_admin", scope=reason,
            started_at=datetime.now(timezone.utc),
        ))
        db.commit()
    return read_service._provider_view(acct, full_access=True)
