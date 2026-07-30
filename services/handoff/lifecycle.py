"""Handoff package lifecycle transitions shared across surfaces (S30).

`revoke_package` is the single revoke transition used by BOTH the creator route
(`POST /api/handoff/{id}/revoke`, S17.5) and the admin governance route
(`POST /api/admin/packages/{id}/revoke`, S30). Centralizing it guarantees admin
revoke has *identical* lifecycle semantics to creator revoke — mark the package
revoked, revoke the recipient grant, and kill any live session so an already-issued
bearer cannot outlive the revoke — differing only in the audit actor/action/metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.db import models as orm
from services.handoff.audit import write_handoff_audit


def revoke_package(
    db: Session,
    pkg: orm.HandoffPackage,
    *,
    actor: str,
    action: str,
    extra_metadata: dict | None = None,
) -> datetime:
    """Revoke ``pkg`` (must be caller-verified as revokable) and write a safe audit
    row. Returns the revoke timestamp. ``extra_metadata`` is merged into the audit
    metadata (safe scalars only — sanitized by write_handoff_audit)."""
    now = datetime.now(timezone.utc)
    pkg.status = "revoked"
    pkg.revoked_at = now
    pkg.updated_at = now

    recipient = db.execute(
        select(orm.HandoffRecipient).where(orm.HandoffRecipient.package_id == pkg.id)
    ).scalar_one_or_none()
    if recipient is not None and recipient.revoked_at is None:
        recipient.revoked_at = now
    # Kill any live sessions so an already-issued bearer cannot outlive the revoke.
    db.execute(
        orm.HandoffRecipientSession.__table__.update()
        .where(
            orm.HandoffRecipientSession.package_id == pkg.id,
            orm.HandoffRecipientSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    db.commit()

    write_handoff_audit(
        db, package_id=pkg.id, lineage_id=pkg.lineage_id,
        actor=actor, action=action,
        metadata={"revoked_at": now.isoformat(), **(extra_metadata or {})},
    )
    return now
