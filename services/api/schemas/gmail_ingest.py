"""Demo-side date-windowed Gmail ingest DTOs (S16.0, D-S16.0-9).

Request bodies carry only calendar-date strings + a safety cap. They never carry
OAuth tokens — the backend reads the Gmail token from its environment
(GMAIL_TOKEN / GMAIL_TOKEN_<id>); the browser never sees or sends it.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DateWindowRequest(BaseModel):
    # Raw YYYY-MM-DD strings; validated server-side by the same parser the CLI
    # uses (services.ingest.list_options.parse_date_window). None = open bound.
    date_from: str | None = None
    date_to: str | None = None
    # Safety cap inside the window (business filter is the date range).
    max_messages: int = Field(default=500, ge=1, le=50_000)


class IngestConfirmRequest(DateWindowRequest):
    # A live (persisting) ingest requires an explicit confirm.
    confirm: bool = False


class IngestWindowResponse(BaseModel):
    date_from: str | None
    date_to: str | None
    open_ended: bool
    provider_filter_applied: bool
    count: int                    # matching messages (preview) or ingested (confirm)
    is_estimate: bool             # True when capped (count is a lower bound)
    cap_hit: bool
    persisted: bool
    sync_token_disposition: str   # human string; windowed snapshots never save one
