"""Creator-side handoff package endpoints — draft/scope/generate/publish/version.

  POST  /api/handoff/{mailbox_id}              create a draft package
  PATCH /api/handoff/{package_id}/scope        set/update scope (draft|generated)
  POST  /api/handoff/{package_id}/generate     (re)generate the candidate
  GET   /api/handoff/{package_id}              creator review view
  POST  /api/handoff/{package_id}/publish      freeze + grant one recipient (S17.5)
  POST  /api/handoff/{package_id}/revoke       block recipient access (S17.5)
  POST  /api/handoff/{package_id}/new-version  new draft in the same lineage (S17.10)

Published packages are immutable: to change scope/evidence or re-share, the
creator forks a NEW version in the same lineage (POST /new-version) and publishes
it; publishing supersedes the prior published version and blocks its recipient.
Manager approval and multi-recipient are intentionally NOT implemented here.

Creator auth boundary: these endpoints require mailbox-owner authorization. In
local/demo mode this uses the existing operator / mailbox-id context; production
package creation requires real owner authentication before customer use (no
production owner auth is implied by this slice).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.db import models as orm
from services.handoff.audit import write_handoff_audit
from services.handoff.export_html import render_package_html
from services.handoff.generator import generate_candidate, generation_diagnostic
from services.handoff.tokens import actor_hash_prefix, hash_token, new_capability_code
from services.ingest.list_options import DateWindowError, parse_date_window

from ..deps import get_db
from ..schemas.handoff import (
    CreateHandoffRequest,
    GenerationDiagnostic,
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
# A new version can only fork a package that is already frozen (a still-editable
# draft/generated package should be edited in place, not forked).
_VERSIONABLE_STATES = {"published", "revoked", "superseded"}
# Only a frozen package can be exported (a draft/generated package is not a
# finished artifact — its evidence may still change on the next generate).
_EXPORTABLE_STATES = {"published", "revoked", "superseded"}
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

    # Creator-only: explain an empty generated candidate (S17.13) so the UI can
    # tell "no events for this mailbox" (widening won't help) apart from a
    # scope/policy miss. Only computed for the empty-generated case.
    generation = None
    if pkg.status == "generated" and not claims and not evidence:
        diag = generation_diagnostic(db, pkg, scope)
        generation = GenerationDiagnostic(code=diag["code"], event_count=diag["event_count"])

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
        generation=generation,
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

    # New-version re-share (S17.10): publishing supersedes any package in the same
    # lineage that is still 'published'. The old package flips to 'superseded'
    # (blocked by the recipient access check), its recipient grant is revoked, and
    # its live sessions are killed — so old access stops the moment the successor
    # goes live. Old rows + audit are retained.
    superseded = _supersede_prior_published(db, pkg, now)
    db.commit()

    write_handoff_audit(
        db, package_id=pkg.id, lineage_id=pkg.lineage_id,
        actor=f"owner:{pkg.creator_email}", action="package_published",
        metadata={
            "recipient_email": recipient_email,
            "expires_at": expires_at.isoformat(),
            "evidence": int(evidence_count),
            "recipient_hash_prefix": actor_hash_prefix(code_hash),
            "version": pkg.version,
        },
    )
    for old_id, old_version in superseded:
        write_handoff_audit(
            db, package_id=old_id, lineage_id=pkg.lineage_id,
            actor=f"owner:{pkg.creator_email}", action="package_superseded",
            metadata={
                "old_package_id": old_id, "new_package_id": pkg.id,
                "lineage_id": pkg.lineage_id,
                "old_version": old_version, "new_version": pkg.version,
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


def _supersede_prior_published(
    db: Session, new_pkg: orm.HandoffPackage, now: datetime
) -> list[tuple[str, int]]:
    """Mark any still-'published' package in the lineage (other than ``new_pkg``)
    as superseded, revoke its recipient grant, and kill its live sessions.

    Returns ``[(old_package_id, old_version), …]`` for audit. No raw code/token is
    read or written; only status/timestamps change. Old rows are retained.
    """
    prior = db.execute(
        select(orm.HandoffPackage).where(
            orm.HandoffPackage.lineage_id == new_pkg.lineage_id,
            orm.HandoffPackage.id != new_pkg.id,
            orm.HandoffPackage.status == "published",
        )
    ).scalars().all()
    result: list[tuple[str, int]] = []
    for old in prior:
        old.status = "superseded"
        old.updated_at = now
        old_recipient = db.execute(
            select(orm.HandoffRecipient).where(orm.HandoffRecipient.package_id == old.id)
        ).scalar_one_or_none()
        if old_recipient is not None and old_recipient.revoked_at is None:
            old_recipient.revoked_at = now
        db.execute(
            orm.HandoffRecipientSession.__table__.update()
            .where(
                orm.HandoffRecipientSession.package_id == old.id,
                orm.HandoffRecipientSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        result.append((old.id, old.version))
    return result


@router.post("/handoff/{package_id}/new-version", response_model=HandoffPackageOut)
async def new_version_handoff(
    package_id: str, db: Session = Depends(get_db)
) -> HandoffPackageOut:
    """Fork a frozen package into a fresh DRAFT in the same lineage (S17.10).

    The recovery path for a lost link, a wrong recipient, a needed scope revision,
    or a revoked package: published packages are immutable, so instead of mutating
    one, the creator creates ``version = max(lineage version) + 1`` — a new draft
    that copies the previous scope but NOT its claims/evidence, recipient,
    sessions, capability code, or published/expiry/revoked timestamps. Evidence is
    re-snapshotted under current rules on the next Generate; publishing then
    supersedes the prior published version.

    Only a frozen package (published/revoked/superseded) can be forked — a
    draft/generated package is still editable in place.
    """
    old = _get_package(db, package_id)
    if old.status not in _VERSIONABLE_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"a new version can only fork a published/revoked/superseded "
            f"package (status '{old.status}')",
        )

    max_version = db.execute(
        select(func.max(orm.HandoffPackage.version))
        .where(orm.HandoffPackage.lineage_id == old.lineage_id)
    ).scalar_one()

    new_pkg = orm.HandoffPackage(
        mailbox_id=old.mailbox_id, creator_email=old.creator_email,
        creator_person_id=old.creator_person_id, status="draft",
        reason=old.reason, title=old.title, policy_mode=old.policy_mode,
        version=(max_version or old.version) + 1,
        supersedes_package_id=old.id, lineage_id=old.lineage_id,
    )
    db.add(new_pkg)
    db.flush()

    # Copy the previous scope verbatim into the new draft; claims/evidence are NOT
    # copied — they are re-snapshotted freshly on Generate under current rules.
    old_scope = db.get(orm.HandoffScope, old.id)
    db.add(orm.HandoffScope(
        package_id=new_pkg.id,
        date_from=old_scope.date_from if old_scope else None,
        date_to=old_scope.date_to if old_scope else None,
        included_project_ids=list(old_scope.included_project_ids) if old_scope else [],
        included_person_ids=list(old_scope.included_person_ids) if old_scope else [],
        included_thread_ids=list(old_scope.included_thread_ids) if old_scope else [],
        excluded_thread_ids=list(old_scope.excluded_thread_ids) if old_scope else [],
        excluded_message_id_headers=list(old_scope.excluded_message_id_headers) if old_scope else [],
        allowed_domains=list(old_scope.allowed_domains) if old_scope else [],
        keyword_filters=list(old_scope.keyword_filters) if old_scope else [],
    ))
    db.commit()

    write_handoff_audit(
        db, package_id=new_pkg.id, lineage_id=new_pkg.lineage_id,
        actor=f"owner:{new_pkg.creator_email}", action="package_version_created",
        metadata={
            "old_package_id": old.id, "new_package_id": new_pkg.id,
            "lineage_id": new_pkg.lineage_id,
            "old_version": old.version, "new_version": new_pkg.version,
        },
    )
    return _package_out(db, new_pkg)


@router.get("/handoff/{package_id}/export.html", response_class=HTMLResponse)
async def export_handoff_html(
    package_id: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Creator-side static HTML export of a frozen package (S17.11).

    A self-contained, read-only snapshot for demo portability / offline handoff /
    compliance archive. Only a frozen package (published/revoked/superseded) can
    be exported; a draft/generated one is not a finished artifact (409).

    Reads only package-local `handoff_*` rows — never the live mailbox. The
    document has the same privacy posture as the recipient view: no mailbox id, no
    exclusion counts, no Gmail/source/open_url link, no capability code or session
    token. All text is HTML-escaped by the renderer.
    """
    pkg = _get_package(db, package_id)
    if pkg.status not in _EXPORTABLE_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"only a frozen package (published/revoked/superseded) can be "
            f"exported (status '{pkg.status}')",
        )

    claims = list(db.execute(
        select(orm.HandoffClaim).where(orm.HandoffClaim.package_id == pkg.id)
        .order_by(orm.HandoffClaim.kind, orm.HandoffClaim.id)
    ).scalars())
    evidence = list(db.execute(
        select(orm.HandoffEvidence).where(orm.HandoffEvidence.package_id == pkg.id)
        .order_by(orm.HandoffEvidence.ts)
    ).scalars())
    recipient = db.execute(
        select(orm.HandoffRecipient).where(orm.HandoffRecipient.package_id == pkg.id)
    ).scalar_one_or_none()

    html_doc = render_package_html(
        pkg, claims, evidence,
        recipient_email=recipient.recipient_email if recipient else None,
    )

    write_handoff_audit(
        db, package_id=pkg.id, lineage_id=pkg.lineage_id,
        actor=f"owner:{pkg.creator_email}", action="package_exported",
        metadata={
            "version": pkg.version, "status": pkg.status,
            "claims": len(claims), "evidence": len(evidence),
        },
    )

    filename = f"handoff-package-v{int(pkg.version)}.html"
    return HTMLResponse(
        content=html_doc,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
