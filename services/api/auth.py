"""S22 — auth + tenant boundary (implements docs/s19-auth-tenant-boundary-plan.md).

A minimal, fail-closed authorization layer applied to every creator/mailbox-scoped
route. Two modes, selected by the ``AUTH_MODE`` env var (read dynamically so tests
can toggle it):

* ``AUTH_MODE=dev`` — a fixed synthetic **dev principal** owns local/demo
  mailboxes, preserving today's localhost workflow (type a mailbox id and go).
  Raw mailbox-id loading is allowed only here.
* ``AUTH_MODE=production`` (also the default when unset/unknown) — every request
  needs an authenticated ``Principal``. No login/session is wired yet in S22
  (real IdP/session lands in a later sprint), so production **fails closed**
  (401). Tests inject a Principal via ``app.dependency_overrides[get_principal]``.

Ownership (S19 §3/§4): a Mailbox is owned by exactly one User in exactly one
Tenant. A not-found, cross-tenant, or not-owned resource returns **404** (never
403) so the API is not a cross-tenant existence oracle. Recipient routes are NOT
guarded here — they keep their capability-code + session auth and stay
package-local snapshot only (S19 §5).
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from services.db import models as orm

from .deps import get_db

# Fixed local dev identity — MUST match alembic/versions/0010_auth_tenant.py.
DEV_TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEV_USER_ID = "22222222-2222-2222-2222-222222222222"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller for a request."""

    user_id: str
    tenant_id: str
    email: str
    roles: frozenset[str]
    is_dev: bool = False


DEV_PRINCIPAL = Principal(
    user_id=DEV_USER_ID,
    tenant_id=DEV_TENANT_ID,
    email="dev@localhost",
    roles=frozenset({"creator", "admin"}),
    is_dev=True,
)


def get_auth_mode() -> str:
    """`dev` only when explicitly set; anything else (incl. unset) is production."""
    return "dev" if os.environ.get("AUTH_MODE", "").strip().lower() == "dev" else "production"


def _looks_like_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _resolve_production_principal(request: Request, db: Session) -> Principal | None:
    """S22 has no login/session wired yet, so production has no principal source
    and fails closed. A later sprint (real IdP/session) fills this in; tests
    override ``get_principal`` directly."""
    return None


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Resolve the request principal, or fail closed."""
    if get_auth_mode() == "dev":
        return DEV_PRINCIPAL
    principal = _resolve_production_principal(request, db)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return principal


def _audit_denied(db: Session, *, mailbox_id: str, principal: Principal, action: str) -> None:
    """Best-effort safe audit of an authorization denial on an existing resource.
    Safe metadata only (ids/action) — never content/tokens. Never masks the 404."""
    try:
        db.add(
            orm.AuditLog(
                mailbox_id=mailbox_id,
                actor=principal.user_id,
                action="authz_denied",
                scope=action,
                started_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    except Exception:
        db.rollback()


def _resolve_owned_mailbox(
    db: Session, principal: Principal, mailbox_id: str
) -> orm.Mailbox | None:
    """Return the mailbox iff the principal owns it; else None (caller → 404)."""
    if not _looks_like_uuid(mailbox_id):
        return None
    mbx = db.get(orm.Mailbox, mailbox_id)
    if mbx is None:
        return None  # not found — nothing to audit
    if principal.is_dev:
        return mbx  # dev principal owns local/demo mailboxes (localhost workflow)
    if (
        mbx.owner_user_id is not None
        and str(mbx.owner_user_id) == principal.user_id
        and str(mbx.tenant_id) == principal.tenant_id
    ):
        return mbx
    # Exists but not owned / cross-tenant — the security-relevant denial.
    _audit_denied(db, mailbox_id=str(mbx.id), principal=principal, action="mailbox_access")
    return None


def require_owner_mailbox(
    mailbox_id: str | None = None,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> orm.Mailbox | None:
    """Guard for mailbox-scoped routes. Reads the ``mailbox_id`` path/query param.
    If absent (e.g. preflight with no mailbox), an authenticated principal is still
    required (via get_principal) but there is no mailbox to own."""
    if mailbox_id is None:
        return None
    mbx = _resolve_owned_mailbox(db, principal, mailbox_id)
    if mbx is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return mbx


def owned_mailbox_or_none(db: Session, principal: Principal, mailbox_id: str | None) -> orm.Mailbox | None:
    """Public ownership check for callers that resolve the mailbox id themselves
    (e.g. jobs: job → mailbox). Returns the mailbox iff the principal owns it."""
    if mailbox_id is None:
        return None
    return _resolve_owned_mailbox(db, principal, mailbox_id)


def require_owner_package(
    package_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> orm.HandoffPackage:
    """Guard for handoff package routes: resolve package → its mailbox → owner."""
    pkg = db.get(orm.HandoffPackage, package_id) if _looks_like_uuid(package_id) else None
    if pkg is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if _resolve_owned_mailbox(db, principal, str(pkg.mailbox_id)) is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return pkg


# ── Governance role guards (S29 — admin/audit viewer, docs/s28-admin-audit-ops-plan.md) ─
# Read-only admin surface roles. These gate the /api/admin/* routes; they do NOT
# grant mailbox-content access — the admin read models expose safe metadata only,
# tenant-scoped by principal.tenant_id (cross-tenant rows are simply absent / 404).
# A wrong in-tenant role → 403; unauthenticated (production) → 401 via get_principal.
def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if "admin" in principal.roles:
        return principal
    raise HTTPException(status_code=403, detail="Admin role required.")


def require_admin_or_reviewer(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.roles & {"admin", "security_reviewer"}:
        return principal
    raise HTTPException(status_code=403, detail="Admin or security-reviewer role required.")
