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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from services.admin import contracts, read_service

from ..auth import Principal, require_admin, require_admin_or_reviewer
from ..deps import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


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
