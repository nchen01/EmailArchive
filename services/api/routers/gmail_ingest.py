"""Demo-side date-windowed Gmail ingest endpoints (S16.0, D-S16.0-8/-9).

The smallest backend seam the demo frontend needs to run the SAME validated
date-window behavior as the CLI:

  POST /api/gmail-ingest/{mailbox_id}/preview   body: {date_from, date_to, max_messages}
  POST /api/gmail-ingest/{mailbox_id}/ingest    body: {..., confirm, replace_snapshot, internal_domains}

Guarantees (match the CLI / D-S16.0):
  - Date validation is the SAME parser as the CLI (parse_date_window) and runs
    before any Gmail/DB work — invalid dates / inverted windows → 422.
  - Account guard: the token's Gmail account must match the mailbox owner
    (getProfile) BEFORE any listing/fetch → 409 on mismatch.
  - Preview lists message IDs only: no raw body fetch, no L0/L1 persistence, no
    sync token, never replaces.
  - A live ingest requires an explicit ``confirm: true`` and is a scoped snapshot
    (sync token bypassed and never saved). ``replace_snapshot: true`` (requires
    confirm) clears the mailbox's existing derived data first; otherwise the run
    is an honest append/upsert.
  - Both endpoints write audit_log rows (start / finish / error). OAuth tokens,
    raw message content, and raw exception messages are never logged or returned.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from services.db import models as orm
from services.db.store import write_audit_event
from services.ingest.gmail_windowed import (
    AccountMismatchError,
    authorized_gmail_provider,
    plan_window,
    verify_account,
)
from services.oauth.flow import ProviderNotConnected
# S25: the live ingest now runs in the `gmail_ingest_window` job handler, not here.
# Re-exported so it stays a named seam (the handler + tests reference this symbol);
# the endpoint itself no longer calls it.
from services.ingest.gmail_windowed import run_windowed_ingest as run_windowed_ingest  # noqa: F401
from services.ingest.list_options import DateWindowError, ListOptions, parse_date_window

from ..auth import Principal, get_principal, require_owner_mailbox
from ..deps import get_db
from ..schemas.gmail_ingest import (
    DateWindowRequest,
    IngestConfirmRequest,
    IngestJobResponse,
    IngestWindowResponse,
)

router = APIRouter(tags=["gmail-ingest"])

_log = logging.getLogger(__name__)
_ACTOR = "api:gmail-ingest"
_SCOPE = "gmail.readonly"


# Seam so tests can inject a fake provider instead of authorizing against Gmail.
# Production resolves the Gmail grant via the S23 vault-backed connected account;
# a missing connected account (production) fails fast as 409 (no job enqueued).
def _provider_for(db: Session, mailbox_token_id: str):
    try:
        return authorized_gmail_provider(db, mailbox_token_id)
    except ProviderNotConnected:
        raise HTTPException(
            status_code=409,
            detail="no connected Gmail account for this mailbox — connect Gmail first",
        ) from None


def _validate_window(body: DateWindowRequest) -> ListOptions:
    """Same validation as the CLI; malformed/inverted → 422 (before any DB/Gmail)."""
    try:
        return parse_date_window(body.date_from, body.date_to)
    except DateWindowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _get_gmail_mailbox(db: Session, mailbox_id: str) -> orm.Mailbox:
    try:
        mailbox_uuid = UUID(mailbox_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="mailbox not found") from None
    mbx = db.get(orm.Mailbox, mailbox_uuid)
    if mbx is None:
        raise HTTPException(status_code=404, detail="mailbox not found")
    if mbx.provider != "gmail":
        raise HTTPException(
            status_code=400, detail="date-window ingest is supported for gmail mailboxes only"
        )
    return mbx


def _clean_internal_domains(body: IngestConfirmRequest) -> list[str] | None:
    """Validate + normalize request-supplied internal_domains. NO DB write.

    Returns the cleaned list when the request supplied any, or ``None`` when the
    request omitted the field (caller falls back to mailbox.config). Raises 422
    if the field was supplied but resolves to nothing usable. Persisting to
    mailbox.config happens ONLY after a successful account-verified ingest (P1.2).
    """
    if body.internal_domains is None:
        return None
    cleaned = [d.strip().lower() for d in body.internal_domains if d and d.strip()]
    if not cleaned:
        raise HTTPException(
            status_code=422,
            detail="internal_domains, when provided, must be non-empty strings",
        )
    return cleaned


def _window_fields(body: DateWindowRequest, options: ListOptions) -> dict:
    return {
        "date_from": body.date_from,
        "date_to": body.date_to,
        "open_ended": options.date_from is None or options.date_to is None,
        "provider_filter_applied": options.is_windowed(),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.post(
    "/gmail-ingest/{mailbox_id}/preview", response_model=IngestWindowResponse,
    dependencies=[Depends(require_owner_mailbox)],
)
async def preview_window(
    mailbox_id: str, body: DateWindowRequest, db: Session = Depends(get_db)
) -> IngestWindowResponse:
    # Validate dates FIRST (matches the CLI order; no DB/Gmail on bad input).
    options = _validate_window(body)
    mbx = _get_gmail_mailbox(db, mailbox_id)
    provider = _provider_for(db, mailbox_id)

    started = _now()
    write_audit_event(
        db, mailbox_id=mailbox_id, actor=_ACTOR, action="ingest_start",
        scope=_SCOPE, started_at=started,
    )
    try:
        verify_account(provider, mbx.owner_email)  # getProfile — before listing
        plan = plan_window(provider, options, body.max_messages)
    except AccountMismatchError as exc:
        _audit_error(db, mailbox_id, started)
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 — sanitized, never leak provider data
        _log.error("gmail_preview_failed", extra={"error_type": type(exc).__name__})
        _audit_error(db, mailbox_id, started)
        raise HTTPException(status_code=502, detail="gmail preview failed") from None

    write_audit_event(
        db, mailbox_id=mailbox_id, actor=_ACTOR, action="ingest_finish",
        scope=_SCOPE, message_count=plan.count, started_at=started, finished_at=_now(),
    )
    return IngestWindowResponse(
        count=plan.count,
        is_estimate=plan.is_estimate,
        cap_hit=plan.hit_cap,
        persisted=False,
        mode="preview",
        replaced=False,
        sync_token_disposition="not_saved (preview)",
        **_window_fields(body, options),
    )


@router.post(
    "/gmail-ingest/{mailbox_id}/ingest", response_model=IngestJobResponse,
    dependencies=[Depends(require_owner_mailbox)],
)
async def ingest_window(
    mailbox_id: str,
    body: IngestConfirmRequest,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> IngestJobResponse:
    """S25: validate + verify the account request-time (preserving the S16.0
    fail-fast safeguards), then ENQUEUE a `gmail_ingest_window` job on the S24
    runner. The heavy fetch/normalize/persist runs in the job; poll
    `GET /api/jobs/{job_id}` for status/progress."""
    options = _validate_window(body)
    mbx = _get_gmail_mailbox(db, mailbox_id)
    if not body.confirm:
        # confirm gates ALL live ingest, and replace_snapshot in particular.
        raise HTTPException(
            status_code=400,
            detail="confirm=true is required for a live date-windowed ingest"
            + (" (replace_snapshot is destructive)" if body.replace_snapshot else ""),
        )
    # P1: a destructive replace MUST have an explicit date window, so it can never
    # wipe the mailbox and refill it with only an incremental delta.
    if body.replace_snapshot and not options.is_windowed():
        raise HTTPException(
            status_code=400,
            detail="replace_snapshot requires date_from and/or date_to so the "
                   "replacement snapshot has an explicit window",
        )
    # Validate request internal_domains but DO NOT persist yet — persisted only on
    # a successful ingest, now inside the job (P1.2).
    requested_domains = _clean_internal_domains(body)

    # Account guard request-time (getProfile) so a mismatch fails fast with 409,
    # before any job is enqueued — preserving the S16.0 safeguard.
    provider = _provider_for(db, mailbox_id)
    started = _now()
    try:
        verify_account(provider, mbx.owner_email)
    except AccountMismatchError as exc:
        _audit_error(db, mailbox_id, started)
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 — sanitized, never leak provider data
        _log.error("gmail_ingest_preflight_failed", extra={"error_type": type(exc).__name__})
        _audit_error(db, mailbox_id, started)
        raise HTTPException(status_code=502, detail="gmail ingest preflight failed") from None

    # Enqueue the ingest job on the S24 runner (importing handlers registers the type).
    import services.jobs.handlers  # noqa: F401 — ensure gmail_ingest_window registered
    from services.jobs import service

    params: dict = {
        "mailbox_id": mailbox_id,
        "date_from": body.date_from,
        "date_to": body.date_to,
        "max_messages": body.max_messages,
        "replace_snapshot": bool(body.replace_snapshot),
    }
    if requested_domains is not None:
        params["internal_domains"] = requested_domains

    job = service.enqueue(
        db, tenant_id=principal.tenant_id, job_type="gmail_ingest_window",
        mailbox_id=mailbox_id, requested_by=principal.user_id, params=params,
        # Dedupe repeated clicks of the same window/replace into one active job.
        idempotency_key=(
            f"gmail_ingest:{mailbox_id}:{body.date_from}:{body.date_to}:{int(bool(body.replace_snapshot))}"
        ),
    )
    return IngestJobResponse(
        job_id=str(job.id), status=job.status,
        mode="replace" if body.replace_snapshot else "append_upsert",
    )


def _audit_error(db: Session, mailbox_id: str, started: datetime) -> None:
    """Write an ingest_error audit row after sanitizing session state.

    Rolls back any aborted transaction first so the audit write itself succeeds;
    records no exception message (which could contain provider/request data).
    """
    try:
        db.rollback()
    except Exception:  # pragma: no cover - best-effort
        pass
    try:
        write_audit_event(
            db, mailbox_id=mailbox_id, actor=_ACTOR, action="ingest_error",
            scope=_SCOPE, started_at=started, finished_at=_now(),
        )
    except Exception:  # pragma: no cover - never mask the original failure
        pass
