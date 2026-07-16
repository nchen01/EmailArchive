"""Creator-side handoff package endpoints — draft/scope/generate slice (S17.3).

  POST  /api/handoff/{mailbox_id}            create a draft package
  PATCH /api/handoff/{package_id}/scope      set/update scope (draft|generated)
  POST  /api/handoff/{package_id}/generate   (re)generate the candidate
  GET   /api/handoff/{package_id}            creator review view

Publish, recipient access, session/capability-code exchange, the package "ask"
endpoint, and manager approval are intentionally NOT implemented here.

Creator auth boundary: these endpoints require mailbox-owner authorization. In
local/demo mode this uses the existing operator / mailbox-id context; production
package creation requires real owner authentication before customer use (no
production owner auth is implied by this slice).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.db import models as orm
from services.handoff.audit import write_handoff_audit
from services.handoff.generator import generate_candidate
from services.handoff.tokens import actor_hash_prefix, hash_token, new_capability_code
from services.ingest.list_options import DateWindowError, parse_date_window

from ..deps import get_db
from ..schemas.handoff import (
    CreateHandoffRequest,
    HandoffClaimOut,
    HandoffEvidenceOut,
    HandoffPackageOut,
    HandoffScopeOut,
    PublishRequest,
    PublishResponse,
    ScopeRequest,
)

router = APIRouter(tags=["handoff"])

_VALID_REASONS = {"vacation", "leave", "transfer", "delegation", "other"}
# Scope/generate are only legal while the package is still being drafted.
_MUTABLE_STATES = {"draft", "generated"}
# Publish is only legal from a generated candidate (draft must generate first).
_DEFAULT_EXPIRY_DAYS = 30


def _uuid_or_404(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(status_code=404, detail="not found") from None


def _get_mailbox(db: Session, mailbox_id: str) -> orm.Mailbox:
    mbx = db.get(orm.Mailbox, _uuid_or_404(mailbox_id))
    if mbx is None:
        raise HTTPException(status_code=404, detail="mailbox not found")
    return mbx


def _get_package(db: Session, package_id: str) -> orm.HandoffPackage:
    pkg = db.get(orm.HandoffPackage, _uuid_or_404(package_id))
    if pkg is None:
        raise HTTPException(status_code=404, detail="handoff package not found")
    return pkg


def _validate_uuid_list(values: list[str], field: str) -> list[str]:
    out: list[str] = []
    for v in values:
        try:
            out.append(str(uuid.UUID(v)))
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=f"{field} must be UUIDs") from None
    return out


def _package_out(db: Session, pkg: orm.HandoffPackage) -> HandoffPackageOut:
    scope = db.get(orm.HandoffScope, pkg.id)
    claims = list(db.execute(
        select(orm.HandoffClaim).where(orm.HandoffClaim.package_id == pkg.id)
        .order_by(orm.HandoffClaim.kind, orm.HandoffClaim.id)
    ).scalars())
    evidence = list(db.execute(
        select(orm.HandoffEvidence).where(orm.HandoffEvidence.package_id == pkg.id)
        .order_by(orm.HandoffEvidence.ts)
    ).scalars())
    excl_rows = db.execute(
        select(orm.HandoffExclusion.exclusion_type, func.count())
        .where(orm.HandoffExclusion.package_id == pkg.id)
        .group_by(orm.HandoffExclusion.exclusion_type)
    ).all()
    exclusion_counts = {etype: int(n) for etype, n in excl_rows}

    return HandoffPackageOut(
        id=pkg.id, mailbox_id=pkg.mailbox_id, creator_email=pkg.creator_email,
        status=pkg.status, reason=pkg.reason, title=pkg.title, version=pkg.version,
        created_at=pkg.created_at.isoformat(), updated_at=pkg.updated_at.isoformat(),
        published_at=pkg.published_at.isoformat() if pkg.published_at else None,
        expires_at=pkg.expires_at.isoformat() if pkg.expires_at else None,
        revoked_at=pkg.revoked_at.isoformat() if pkg.revoked_at else None,
        scope=HandoffScopeOut(
            date_from=scope.date_from.isoformat() if scope and scope.date_from else None,
            date_to=scope.date_to.isoformat() if scope and scope.date_to else None,
            included_project_ids=list(scope.included_project_ids) if scope else [],
            included_person_ids=list(scope.included_person_ids) if scope else [],
            included_thread_ids=list(scope.included_thread_ids) if scope else [],
            excluded_thread_ids=list(scope.excluded_thread_ids) if scope else [],
            excluded_message_id_headers=list(scope.excluded_message_id_headers) if scope else [],
            allowed_domains=list(scope.allowed_domains) if scope else [],
            keyword_filters=list(scope.keyword_filters) if scope else [],
        ),
        claims=[HandoffClaimOut(
            id=c.id, kind=c.kind, text=c.text, project_id=c.project_id,
            source_message_id_headers=list(c.source_message_id_headers),
            confidence=float(c.confidence),
        ) for c in claims],
        evidence=[HandoffEvidenceOut(
            message_id_header=e.message_id_header, subject=e.subject,
            sender_display=e.sender_display, sender_domain=e.sender_domain,
            date=e.ts.isoformat() if e.ts else "", body_snapshot=e.body_snapshot,
            source_type=e.source_type,
        ) for e in evidence],
        exclusion_counts=exclusion_counts,
    )


@router.post("/handoff/{mailbox_id}", response_model=HandoffPackageOut)
async def create_handoff(
    mailbox_id: str, body: CreateHandoffRequest, db: Session = Depends(get_db)
) -> HandoffPackageOut:
    mbx = _get_mailbox(db, mailbox_id)
    if body.reason not in _VALID_REASONS:
        raise HTTPException(status_code=422, detail=f"reason must be one of {sorted(_VALID_REASONS)}")

    pkg = orm.HandoffPackage(
        mailbox_id=mbx.id, creator_email=mbx.owner_email,
        creator_person_id=mbx.owner_person_id, status="draft",
        reason=body.reason, title=body.title, lineage_id=str(uuid.uuid4()),
    )
    db.add(pkg)
    db.flush()
    db.add(orm.HandoffScope(package_id=pkg.id))
    db.commit()

    write_handoff_audit(
        db, package_id=pkg.id, lineage_id=pkg.lineage_id,
        actor=f"owner:{mbx.owner_email}", action="handoff_created",
        metadata={"reason": body.reason},
    )
    return _package_out(db, pkg)


@router.patch("/handoff/{package_id}/scope", response_model=HandoffPackageOut)
async def update_scope(
    package_id: str, body: ScopeRequest, db: Session = Depends(get_db)
) -> HandoffPackageOut:
    pkg = _get_package(db, package_id)
    if pkg.status not in _MUTABLE_STATES:
        raise HTTPException(status_code=409, detail=f"scope is immutable in status '{pkg.status}'")

    try:
        window = parse_date_window(body.date_from, body.date_to)
    except DateWindowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    scope = db.get(orm.HandoffScope, pkg.id)
    if scope is None:
        scope = orm.HandoffScope(package_id=pkg.id)
        db.add(scope)
    scope.date_from = window.date_from
    scope.date_to = window.date_to
    scope.included_project_ids = _validate_uuid_list(body.included_project_ids, "included_project_ids")
    scope.included_person_ids = _validate_uuid_list(body.included_person_ids, "included_person_ids")
    scope.included_thread_ids = _validate_uuid_list(body.included_thread_ids, "included_thread_ids")
    scope.excluded_thread_ids = _validate_uuid_list(body.excluded_thread_ids, "excluded_thread_ids")
    scope.excluded_message_id_headers = list(body.excluded_message_id_headers)
    scope.allowed_domains = list(body.allowed_domains)
    scope.keyword_filters = list(body.keyword_filters)
    pkg.updated_at = datetime.now(timezone.utc)
    db.commit()

    write_handoff_audit(
        db, package_id=pkg.id, lineage_id=pkg.lineage_id,
        actor=f"owner:{pkg.creator_email}", action="scope_changed",
        metadata={"has_date_window": window.is_windowed()},
    )
    return _package_out(db, pkg)


@router.post("/handoff/{package_id}/generate", response_model=HandoffPackageOut)
async def generate_package(
    package_id: str, db: Session = Depends(get_db)
) -> HandoffPackageOut:
    pkg = _get_package(db, package_id)
    if pkg.status not in _MUTABLE_STATES:
        raise HTTPException(status_code=409, detail=f"cannot generate in status '{pkg.status}'")
    generate_candidate(db, pkg)
    return _package_out(db, pkg)


@router.get("/handoff/{package_id}", response_model=HandoffPackageOut)
async def get_handoff(package_id: str, db: Session = Depends(get_db)) -> HandoffPackageOut:
    return _package_out(db, _get_package(db, package_id))


@router.post("/handoff/{package_id}/publish", response_model=PublishResponse)
async def publish_handoff(
    package_id: str, body: PublishRequest, db: Session = Depends(get_db)
) -> PublishResponse:
    """Freeze a generated candidate and grant one recipient package-local access.

    Publish is only legal from ``generated`` (a draft must generate first), and
    only if the candidate actually has cited evidence — publishing an empty
    package would hand the recipient nothing to read and no claim could satisfy
    "no citation, no claim", so it is rejected rather than silently allowed.

    The package freezes here: scope/generate already reject any non-mutable
    status (§immutability), so a published package can only change via a new
    version (deferred to S17.6). A one-time capability code is minted; only its
    hash is stored, and the raw code is returned to the creator exactly once.
    """
    pkg = _get_package(db, package_id)
    if pkg.status != "generated":
        raise HTTPException(
            status_code=409,
            detail=f"only a generated package can be published (status '{pkg.status}')",
        )

    recipient_email = body.recipient_email.strip()
    if not recipient_email:
        raise HTTPException(status_code=422, detail="recipient_email is required")

    evidence_count = db.execute(
        select(func.count()).select_from(orm.HandoffEvidence)
        .where(orm.HandoffEvidence.package_id == pkg.id)
    ).scalar_one()
    if evidence_count == 0:
        raise HTTPException(
            status_code=409,
            detail="cannot publish a package with no cited evidence; widen the scope and regenerate",
        )

    now = datetime.now(timezone.utc)
    days = body.expires_in_days or _DEFAULT_EXPIRY_DAYS
    expires_at = now + timedelta(days=days)

    raw_code = new_capability_code()
    code_hash = hash_token(raw_code)

    db.add(orm.HandoffRecipient(
        package_id=pkg.id, recipient_email=recipient_email,
        capability_code_hash=code_hash, granted_at=now, expires_at=expires_at,
    ))
    pkg.status = "published"
    pkg.published_at = now
    pkg.expires_at = expires_at
    pkg.updated_at = now
    db.commit()

    write_handoff_audit(
        db, package_id=pkg.id, lineage_id=pkg.lineage_id,
        actor=f"owner:{pkg.creator_email}", action="package_published",
        metadata={
            "recipient_email": recipient_email,
            "expires_at": expires_at.isoformat(),
            "evidence": int(evidence_count),
            "recipient_hash_prefix": actor_hash_prefix(code_hash),
        },
    )
    return PublishResponse(
        package=_package_out(db, pkg),
        recipient_email=recipient_email,
        expires_at=expires_at.isoformat(),
        capability_code=raw_code,
        share_fragment=f"#c={raw_code}",
    )


@router.post("/handoff/{package_id}/revoke", response_model=HandoffPackageOut)
async def revoke_handoff(
    package_id: str, db: Session = Depends(get_db)
) -> HandoffPackageOut:
    """Revoke a published package: block recipient access immediately.

    Marks the package + recipient revoked and kills any live session. Audit rows
    are retained (no cascade). Only a published package can be revoked.
    """
    pkg = _get_package(db, package_id)
    if pkg.status != "published":
        raise HTTPException(
            status_code=409,
            detail=f"only a published package can be revoked (status '{pkg.status}')",
        )

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
        actor=f"owner:{pkg.creator_email}", action="package_revoked",
        metadata={"revoked_at": now.isoformat()},
    )
    return _package_out(db, pkg)
