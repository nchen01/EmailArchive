"""`calendar_sync_window` job handler (S51 - docs/s49 section 9).

Fetches the connected google_calendar account's events in a date window and upserts
them into the creator-owned `calendar_event` / `calendar_event_attendee` live tables
(idempotent on `(mailbox_id, calendar_item_id)`). Private-visibility events are
excluded at ingest and never persisted. The refresh token never leaves the vault -
the handler resolves a short-lived access token via the S23/S50 vault-backed
resolver. All progress/summary/errors are SAFE COUNTS ONLY (no titles, attendee
emails, tokens, or provider error bodies).

Kill switch: `EKC_CALENDAR_SYNC_DISABLED=1` makes the handler a safe no-op so
calendar sync can be globally disabled without a deploy (S27 fail-closed posture).

S51 is the live/sync layer ONLY - it writes NOTHING recipient-facing and does not
create any package snapshot (handoff_calendar_item is S52).
"""
from __future__ import annotations

import os
from typing import Callable

from services.jobs.registry import JobContext, JobError, JobResult, register


def _kill_switch_on() -> bool:
    return os.environ.get("EKC_CALENDAR_SYNC_DISABLED", "").strip() == "1"


# Seams (tests inject fakes). The grant resolver mints a vault-backed access token
# for the connected google_calendar account; the client lists events read-only.
def _default_grant_resolver(db, mailbox_id: str) -> dict:
    from services.oauth.resolver import resolve_calendar_grant
    return resolve_calendar_grant(db, mailbox_id)


def _default_client():
    from services.calendar.gcal_client import get_calendar_client
    return get_calendar_client()


grant_resolver: Callable[[object, str], dict] = _default_grant_resolver
client_factory: Callable[[], object] = _default_client


def set_grant_resolver(fn: Callable[[object, str], dict]) -> None:
    global grant_resolver
    grant_resolver = fn


def set_client_factory(fn: Callable[[], object]) -> None:
    global client_factory
    client_factory = fn


def _to_rfc3339(date_str: str | None, *, end_of_day: bool) -> str | None:
    if not date_str:
        return None
    return f"{date_str}T23:59:59Z" if end_of_day else f"{date_str}T00:00:00Z"


@register("calendar_sync_window")
def run(ctx: JobContext) -> JobResult:
    from services.db import models as orm
    from services.calendar.normalize import normalize_event
    from services.oauth.flow import ProviderNotConnected

    if _kill_switch_on():
        return JobResult(summary="calendar sync disabled", progress={"phase": "disabled"})

    p = ctx.params
    mailbox_id = p.get("mailbox_id")
    db = ctx._db
    mbx = db.get(orm.Mailbox, mailbox_id) if mailbox_id else None
    if mbx is None:
        raise JobError("mailbox_not_found")

    calendar_id = str(p.get("calendar_id", "primary"))
    max_events = int(p.get("max_events", 250))

    ctx.progress(phase="resolving")
    ctx.check_canceled()
    try:
        grant = grant_resolver(db, mailbox_id)
    except ProviderNotConnected:
        raise JobError("provider_not_connected") from None
    provider_account_id = grant.get("provider_account_id")
    if not provider_account_id:
        raise JobError("provider_not_connected")

    ctx.progress(phase="fetching")
    ctx.check_canceled()
    client = client_factory()
    try:
        raw_events = client.list_events(
            access_token=grant["token"], calendar_id=calendar_id,
            time_min=_to_rfc3339(p.get("date_from"), end_of_day=False),
            time_max=_to_rfc3339(p.get("date_to"), end_of_day=True),
            max_results=max_events,
        )
    except Exception as exc:  # noqa: BLE001 - never surface provider error bodies
        raise JobError("calendar_fetch_failed", type(exc).__name__) from None

    ctx.progress(phase="storing")
    ctx.check_canceled()
    stored = 0
    skipped_private = 0
    for ev in raw_events:
        from services.calendar.normalize import is_private
        if is_private(ev):
            skipped_private += 1
            continue
        norm = normalize_event(ev)
        if norm is None:
            continue  # cancelled / unusable - not counted as private
        _upsert_event(db, mbx, provider_account_id, calendar_id, norm)
        stored += 1
    db.commit()

    return JobResult(
        summary=f"synced {stored} calendar events",
        progress={
            "phase": "done",
            "fetched": len(raw_events),
            "stored": stored,
            "skipped_private": skipped_private,
        },
    )


def _upsert_event(db, mbx, provider_account_id, calendar_id, norm) -> None:
    """Idempotent upsert keyed on (mailbox_id, calendar_item_id). Replaces the
    attendee rows on update so a re-sync reflects the current attendee list."""
    from sqlalchemy import select

    from services.db import models as orm

    row = db.execute(
        select(orm.CalendarEvent).where(
            orm.CalendarEvent.mailbox_id == str(mbx.id),
            orm.CalendarEvent.calendar_item_id == norm.calendar_item_id,
        )
    ).scalar_one_or_none()
    if row is None:
        row = orm.CalendarEvent(
            tenant_id=str(mbx.tenant_id), mailbox_id=str(mbx.id),
            provider_account_id=str(provider_account_id),
            calendar_item_id=norm.calendar_item_id,
        )
        db.add(row)
    row.calendar_label = calendar_id
    row.title = norm.title
    row.starts_at = norm.starts_at
    row.ends_at = norm.ends_at
    row.all_day = norm.all_day
    row.organizer_display = norm.organizer_display or None
    row.organizer_domain = norm.organizer_domain or None
    row.is_recurring = norm.is_recurring
    row.recurrence_summary = norm.recurrence_summary
    row.has_conferencing = norm.has_conferencing
    row.attendee_count = norm.attendee_count
    from datetime import datetime, timezone
    row.synced_at = datetime.now(timezone.utc)
    db.flush()  # ensure row.id for attendee FKs

    db.execute(
        orm.CalendarEventAttendee.__table__.delete().where(
            orm.CalendarEventAttendee.calendar_event_id == row.id
        )
    )
    for a in norm.attendees:
        db.add(orm.CalendarEventAttendee(
            calendar_event_id=row.id, display=a.display or None, domain=a.domain or None,
        ))
