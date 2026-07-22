"""Recipient-side handoff endpoints — session exchange + package view (S17.5).

  POST /api/handoff/recipient/session   exchange a one-time capability code for a
                                        short-lived, package-scoped session
  GET  /api/handoff/recipient/package   read the published package (session-auth)

Hard invariants (S17.2 §8):
- These endpoints read ONLY `handoff_*` rows. They never touch message/thread/
  project/person live-mailbox tables — the recipient view is fully package-local.
- No raw capability token or session token appears in a URL path/query, a log,
  or audit metadata. The code/token arrives in a POST body / Authorization
  header; only sha256 hashes are stored and looked up.
- Access is enforced at REQUEST TIME: every call re-checks `now >= expires_at`
  and revoked/superseded state directly, so access is blocked even if a
  background job has not yet flipped `status` away from `published`.
- Blocked (expired/revoked/superseded/unknown) states return a single neutral
  "no longer available" response — never a reason that could leak package state.
- The recipient sees claims + evidence + a global privacy posture only: no
  exclusion counts, no Gmail/source link, no live mailbox.

Deferred to S17.6+: recipient "ask" (package-scoped cover-for-me) and the
new-version supersede rotation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from services.db import models as orm
from services.handoff.audit import write_handoff_audit
from services.handoff.tokens import (
    SESSION_TTL_MINUTES,
    actor_hash_prefix,
    hash_token,
    new_session_token,
)

from ..deps import get_db
from ..schemas.handoff import (
    PrivacyPosture,
    RecipientClaimOut,
    RecipientEvidenceOut,
    RecipientPackageOut,
    RecipientSessionRequest,
    RecipientSessionResponse,
)

router = APIRouter(tags=["handoff-recipient"])

# One neutral message for every block/unknown case — no oracle about why.
_UNAVAILABLE = "This handoff is no longer available."


def _unavailable() -> HTTPException:
    return HTTPException(status_code=403, detail=_UNAVAILABLE)


def _package_live(pkg: orm.HandoffPackage, recipient: orm.HandoffRecipient, now: datetime) -> bool:
    """Access-time liveness: published, unexpired, unrevoked, not superseded.

    Evaluated directly on every request. `now >= expires_at` blocks even if the
    stored status is still 'published' (status flips are an optimization, not the
    access-control mechanism — S17.2 §4.2).
    """
    if pkg.status != "published":
        return False  # blocks expired / revoked / superseded / any non-live status
    if pkg.revoked_at is not None or recipient.revoked_at is not None:
        return False
    if pkg.expires_at is None or now >= pkg.expires_at:
        return False
    if recipient.expires_at is not None and now >= recipient.expires_at:
        return False
    return True


@router.post("/handoff/recipient/session", response_model=RecipientSessionResponse)
async def start_session(
    body: RecipientSessionRequest, db: Session = Depends(get_db)
) -> RecipientSessionResponse:
    """Exchange the one-time capability code (from the share-link fragment, sent in
    the POST body) for a short-lived, package-scoped bearer session."""
    code_hash = hash_token(body.code)
    recipient = db.execute(
        select(orm.HandoffRecipient).where(
            orm.HandoffRecipient.capability_code_hash == code_hash
        )
    ).scalar_one_or_none()
    # Unknown code and blocked package return the same neutral response — no oracle
    # for whether a package exists behind this code.
    if recipient is None:
        raise _unavailable()

    pkg = db.get(orm.HandoffPackage, recipient.package_id)
    now = datetime.now(timezone.utc)
    if pkg is None or not _package_live(pkg, recipient, now):
        raise _unavailable()

    # One-time code (S17.2 §7.1: "consumed/rotated on exchange"). Atomically claim
    # it with a conditional UPDATE guarded on consumed_at IS NULL: exactly one
    # caller wins even under a concurrent double-exchange, and a replay of an
    # already-spent code matches zero rows. A spent/racing code returns the SAME
    # neutral response as an invalid/expired/revoked one — no oracle, no second
    # session issued.
    claimed = db.execute(
        update(orm.HandoffRecipient)
        .where(
            orm.HandoffRecipient.id == recipient.id,
            orm.HandoffRecipient.capability_code_consumed_at.is_(None),
        )
        .values(capability_code_consumed_at=now)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise _unavailable()

    raw_token = new_session_token()
    token_hash = hash_token(raw_token)
    # Session lifetime is capped by the package expiry: never outlive the grant.
    session_expiry = min(now + timedelta(minutes=SESSION_TTL_MINUTES), pkg.expires_at)

    db.add(orm.HandoffRecipientSession(
        package_id=pkg.id, recipient_id=recipient.id,
        session_token_hash=token_hash, issued_at=now, expires_at=session_expiry,
    ))
    recipient.last_accessed_at = now
    db.commit()

    write_handoff_audit(
        db, package_id=pkg.id, lineage_id=pkg.lineage_id,
        actor=f"recipient:{actor_hash_prefix(code_hash)}", action="recipient_session_started",
        metadata={"session_expires_at": session_expiry.isoformat()},
    )
    return RecipientSessionResponse(
        session_token=raw_token,
        expires_at=session_expiry.isoformat(),
        package_id=pkg.id,
    )


def _session_from_header(db: Session, authorization: str | None):
    """Resolve a live (recipient, package, session) triple from a bearer header.

    Returns None for any missing/invalid/expired/revoked session or blocked
    package — the caller maps that to the single neutral unavailable response.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    raw_token = authorization.split(" ", 1)[1].strip()
    if not raw_token:
        return None
    token_hash = hash_token(raw_token)
    sess = db.execute(
        select(orm.HandoffRecipientSession).where(
            orm.HandoffRecipientSession.session_token_hash == token_hash
        )
    ).scalar_one_or_none()
    if sess is None or sess.revoked_at is not None:
        return None
    now = datetime.now(timezone.utc)
    if now >= sess.expires_at:
        return None
    recipient = db.get(orm.HandoffRecipient, sess.recipient_id)
    pkg = db.get(orm.HandoffPackage, sess.package_id)
    if recipient is None or pkg is None or not _package_live(pkg, recipient, now):
        return None
    return recipient, pkg, sess


@router.get("/handoff/recipient/package", response_model=RecipientPackageOut)
async def get_recipient_package(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> RecipientPackageOut:
    """Return the published package for the caller's session: package-local claims
    + evidence + global privacy posture. No counts, no Gmail/source link, no live
    mailbox. Reads only `handoff_*` rows."""
    resolved = _session_from_header(db, authorization)
    if resolved is None:
        raise _unavailable()
    recipient, pkg, _sess = resolved

    claims = list(db.execute(
        select(orm.HandoffClaim).where(orm.HandoffClaim.package_id == pkg.id)
        .order_by(orm.HandoffClaim.kind, orm.HandoffClaim.id)
    ).scalars())
    evidence = list(db.execute(
        select(orm.HandoffEvidence).where(orm.HandoffEvidence.package_id == pkg.id)
        .order_by(orm.HandoffEvidence.ts)
    ).scalars())

    now = datetime.now(timezone.utc)
    recipient.last_accessed_at = now
    db.commit()

    write_handoff_audit(
        db, package_id=pkg.id, lineage_id=pkg.lineage_id,
        actor=f"recipient:{actor_hash_prefix(recipient.capability_code_hash)}",
        action="recipient_viewed",
        metadata={"claims": len(claims), "evidence": len(evidence)},
    )
    return RecipientPackageOut(
        package_id=pkg.id, title=pkg.title, reason=pkg.reason,
        creator_email=pkg.creator_email,
        published_at=pkg.published_at.isoformat() if pkg.published_at else None,
        expires_at=pkg.expires_at.isoformat() if pkg.expires_at else None,
        claims=[RecipientClaimOut(
            id=c.id, kind=c.kind, text=c.text, project_id=c.project_id,
            source_message_id_headers=list(c.source_message_id_headers),
            confidence=float(c.confidence),
        ) for c in claims],
        evidence=[RecipientEvidenceOut(
            message_id_header=e.message_id_header, subject=e.subject,
            sender_display=e.sender_display, sender_domain=e.sender_domain,
            date=e.ts.isoformat() if e.ts else "", body_snapshot=e.body_snapshot,
            source_type=e.source_type,
        ) for e in evidence],
        privacy_posture=PrivacyPosture(),
    )
