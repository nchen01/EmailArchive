"""Google Calendar sync enqueue endpoint (S51 - docs/s49 section 9).

  POST /api/mailbox/{mailbox_id}/calendar/sync   creator-authed -> enqueue sync job

Owner/tenant guarded (S22). Validates the date window, requires a connected
google_calendar account (fail-fast 409 - no job enqueued otherwise), then enqueues
an idempotent `calendar_sync_window` job on the S24 runner. The worker resolves the
vault-backed access token and fetches/normalizes/upserts calendar events into the
creator-owned live tables; NOTHING here touches tokens or recipient routes.

S51 is the sync/live layer only - no package snapshot, no recipient exposure.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.db import models as orm
from services.ingest.list_options import DateWindowError, parse_date_window
from services.oauth import flow

from ..auth import Principal, get_principal, require_owner_mailbox
from ..deps import get_db

router = APIRouter(tags=["calendar-sync"])

_PROVIDER = "google_calendar"


class CalendarSyncRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    calendar_id: str = "primary"
    max_events: int = Field(default=250, ge=1, le=2500)


class CalendarSyncResponse(BaseModel):
    job_id: str
    status: str


@router.post(
    "/mailbox/{mailbox_id}/calendar/sync",
    response_model=CalendarSyncResponse,
)
async def enqueue_calendar_sync(
    mailbox_id: str,
    body: CalendarSyncRequest,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> CalendarSyncResponse:
    # Validate the window (same parser as S16.0/S25 ingest); malformed -> 422.
    try:
        parse_date_window(body.date_from, body.date_to)
    except DateWindowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    # Require a connected google_calendar account before enqueuing (fail-fast 409).
    acct = flow.get_connected_account(
        db, tenant_id=principal.tenant_id, mailbox_id=str(mbx.id), provider=_PROVIDER
    )
    if acct is None:
        raise HTTPException(status_code=409, detail="No connected Google Calendar account.")

    import services.jobs.handlers  # noqa: F401 - ensure calendar_sync_window registered
    from services.jobs import service

    params: dict = {
        "mailbox_id": str(mbx.id),
        "date_from": body.date_from,
        "date_to": body.date_to,
        "calendar_id": body.calendar_id,
        "max_events": body.max_events,
    }
    # Dedupe repeated syncs of the SAME window into one active job.
    idem = (
        f"calendar_sync:{mbx.id}:{body.date_from}:{body.date_to}:"
        f"{body.calendar_id}:{body.max_events}"
    )
    job = service.enqueue(
        db, tenant_id=principal.tenant_id, job_type="calendar_sync_window",
        mailbox_id=str(mbx.id), requested_by=principal.user_id, params=params,
        idempotency_key=idem,
    )
    return CalendarSyncResponse(job_id=str(job.id), status=job.status)
