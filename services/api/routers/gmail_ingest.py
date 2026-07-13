"""Demo-side date-windowed Gmail ingest endpoints (S16.0, D-S16.0-8/-9).

The smallest backend seam the demo frontend needs to run the SAME validated
date-window behavior as the CLI:

  POST /api/gmail-ingest/{mailbox_id}/preview   body: {date_from, date_to, max_messages}
  POST /api/gmail-ingest/{mailbox_id}/ingest    body: {..., confirm: true}

Guarantees (match the CLI / D-S16.0):
  - Date validation is the SAME parser as the CLI (parse_date_window) and runs
    before any Gmail/DB work — invalid dates / inverted windows → 422.
  - Preview lists message IDs only: no raw body fetch, no L0/L1 persistence, no
    sync token.
  - A live ingest requires an explicit ``confirm: true`` and is a scoped
    snapshot: the sync token is bypassed and never saved.
  - OAuth tokens are never accepted from, returned to, or logged for the browser;
    the backend reads the token from its own environment.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from services.db import models as orm
from services.ingest.gmail_windowed import (
    build_gmail_provider,
    plan_window,
    run_windowed_ingest,
)
from services.ingest.list_options import DateWindowError, ListOptions, parse_date_window

from ..deps import get_db
from ..schemas.gmail_ingest import (
    DateWindowRequest,
    IngestConfirmRequest,
    IngestWindowResponse,
)

router = APIRouter(tags=["gmail-ingest"])


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


def _window_fields(body: DateWindowRequest, options: ListOptions) -> dict:
    return {
        "date_from": body.date_from,
        "date_to": body.date_to,
        "open_ended": options.date_from is None or options.date_to is None,
        "provider_filter_applied": options.is_windowed(),
    }


@router.post("/gmail-ingest/{mailbox_id}/preview", response_model=IngestWindowResponse)
async def preview_window(
    mailbox_id: str, body: DateWindowRequest, db: Session = Depends(get_db)
) -> IngestWindowResponse:
    # Validate dates FIRST (matches the CLI order; no DB/Gmail on bad input).
    options = _validate_window(body)
    _get_gmail_mailbox(db, mailbox_id)
    provider = _provider_for(mailbox_id)
    plan = plan_window(provider, options, body.max_messages)
    return IngestWindowResponse(
        count=plan.count,
        is_estimate=plan.is_estimate,
        cap_hit=plan.hit_cap,
        persisted=False,
        sync_token_disposition="not_saved (preview)",
        **_window_fields(body, options),
    )


@router.post("/gmail-ingest/{mailbox_id}/ingest", response_model=IngestWindowResponse)
async def ingest_window(
    mailbox_id: str, body: IngestConfirmRequest, db: Session = Depends(get_db)
) -> IngestWindowResponse:
    options = _validate_window(body)
    mbx = _get_gmail_mailbox(db, mailbox_id)
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm=true is required for a live date-windowed ingest",
        )
    internal_domains = list((mbx.config or {}).get("internal_domains", []))
    summary = run_windowed_ingest(
        db,
        db_mailbox_id=mailbox_id,
        token_mailbox_id=mailbox_id,
        owner_email=mbx.owner_email,
        internal_domains=internal_domains,
        options=options,
        max_messages=body.max_messages,
    )
    return IngestWindowResponse(
        count=summary["messages"],
        is_estimate=summary["hit_cap"],
        cap_hit=summary["hit_cap"],
        persisted=True,
        sync_token_disposition=summary["sync_token_disposition"],
        **_window_fields(body, options),
    )
