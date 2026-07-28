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
    build_gmail_provider,
    plan_window,
    run_windowed_ingest,
    verify_account,
)
from services.ingest.list_options import DateWindowError, ListOptions, parse_date_window

from ..auth import require_owner_mailbox
from ..deps import get_db
from ..schemas.gmail_ingest import (
    DateWindowRequest,
    IngestConfirmRequest,
    IngestWindowResponse,
)

router = APIRouter(tags=["gmail-ingest"])

_log = logging.getLogger(__name__)
_ACTOR = "api:gmail-ingest"
_SCOPE = "gmail.readonly"


# Seam so tests can inject a fake provider instead of authorizing against Gmail.
def _provider_for(mailbox_token_id: str):
    return build_gmail_provider(mailbox_token_id)


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
    provider = _provider_for(mailbox_id)

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
    "/gmail-ingest/{mailbox_id}/ingest", response_model=IngestWindowResponse,
    dependencies=[Depends(require_owner_mailbox)],
)
async def ingest_window(
    mailbox_id: str, body: IngestConfirmRequest, db: Session = Depends(get_db)
) -> IngestWindowResponse:
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
    # Validate request internal_domains but DO NOT persist yet — a mismatched
    # token or a failed ingest must not mutate mailbox.config (P1.2).
    requested_domains = _clean_internal_domains(body)
    effective_domains = (
        requested_domains
        if requested_domains is not None
        else list((mbx.config or {}).get("internal_domains", []))
    )
    provider = _provider_for(mailbox_id)

    started = _now()
    write_audit_event(
        db, mailbox_id=mailbox_id, actor=_ACTOR, action="ingest_start",
        scope=_SCOPE, started_at=started,
    )
    try:
        verify_account(provider, mbx.owner_email)  # getProfile — before listing/fetch
        summary = run_windowed_ingest(
            db,
            db_mailbox_id=mailbox_id,
            token_mailbox_id=mailbox_id,
            owner_email=mbx.owner_email,
            internal_domains=effective_domains,
            options=options,
            max_messages=body.max_messages,
            replace_snapshot=body.replace_snapshot,
            provider=provider,
        )
    except AccountMismatchError as exc:
        _audit_error(db, mailbox_id, started)
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 — sanitized, never leak provider data
        _log.error("gmail_ingest_failed", extra={"error_type": type(exc).__name__})
        _audit_error(db, mailbox_id, started)
        raise HTTPException(status_code=502, detail="gmail ingest failed") from None

    # Success path only: now persist request-supplied internal_domains to config.
    if requested_domains is not None:
        mbx = db.get(orm.Mailbox, UUID(mailbox_id))
        cfg = dict(mbx.config or {})
        cfg["internal_domains"] = requested_domains
        mbx.config = cfg  # reassign so the JSONB change is tracked
        db.commit()

    write_audit_event(
        db, mailbox_id=mailbox_id, actor=_ACTOR, action="ingest_finish",
        scope=_SCOPE, message_count=summary["messages"],
        started_at=started, finished_at=_now(),
    )
    return IngestWindowResponse(
        count=summary["messages"],
        is_estimate=summary["hit_cap"],
        cap_hit=summary["hit_cap"],
        persisted=True,
        mode="replace" if summary["replaced"] else "append_upsert",
        replaced=summary["replaced"],
        sync_token_disposition=summary["sync_token_disposition"],
        **_window_fields(body, options),
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
