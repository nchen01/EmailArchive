"""Deterministic, rule-based normalization of raw Google Calendar events (S51).

Maps a raw events.list item to the SAFE allow-list (docs/s49 section 3) and applies
the sensitivity rules (section 4). Pure + LLM-free (repo determinism invariant).

Hard rules encoded here:
  - Private / private-visibility (and 'confidential') events are EXCLUDED entirely
    (`is_private` -> True). The caller must skip them BEFORE mapping, so a private
    event's title / organizer / attendees are NEVER read into the model.
  - `description`/body is NEVER read. Attachments, conferencing URLs, transcripts,
    and raw RRULEs are NEVER read - only a has_conferencing boolean and a coarse
    human recurrence summary.
  - Attendees keep display name + email DOMAIN only (never the raw email, never a
    response status - attendance is not a signal this product records).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Visibility values that mean "do not ingest at all" (docs/s49 section 4, LOCKED).
_PRIVATE_VISIBILITY = {"private", "confidential"}


@dataclass
class NormalizedAttendee:
    display: str = ""
    domain: str = ""


@dataclass
class NormalizedEvent:
    calendar_item_id: str
    title: str
    starts_at: datetime | None
    ends_at: datetime | None
    all_day: bool
    organizer_display: str
    organizer_domain: str
    is_recurring: bool
    recurrence_summary: str | None
    has_conferencing: bool
    attendees: list[NormalizedAttendee] = field(default_factory=list)

    @property
    def attendee_count(self) -> int:
        return len(self.attendees)


def _domain_of(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].strip().lower()


def is_private(raw: dict) -> bool:
    """True when the event must be excluded from the MVP entirely (private /
    confidential visibility). LOCKED default - no override path (docs/s49 sec 4)."""
    vis = (raw.get("visibility") or "").strip().lower()
    return vis in _PRIVATE_VISIBILITY


def is_ingestable(raw: dict) -> bool:
    """False for events that never become live rows: cancelled, or private."""
    if (raw.get("status") or "").strip().lower() == "cancelled":
        return False
    if is_private(raw):
        return False
    return True


def _parse_dt(node: dict | None) -> tuple[datetime | None, bool]:
    """Return (datetime, all_day). Google uses `dateTime` for timed events and
    `date` for all-day events."""
    if not node:
        return None, False
    dt = node.get("dateTime")
    if dt:
        try:
            return datetime.fromisoformat(dt.replace("Z", "+00:00")), False
        except ValueError:
            return None, False
    d = node.get("date")
    if d:
        try:
            return datetime.fromisoformat(d).replace(tzinfo=timezone.utc), True
        except ValueError:
            return None, True
    return None, False


def _recurrence_summary(raw: dict) -> tuple[bool, str | None]:
    """A coarse, human recurrence label from RRULE FREQ - NEVER the raw RRULE
    (which can encode exception dates / counts). Returns (is_recurring, summary)."""
    rules = raw.get("recurrence")
    recurring = bool(rules) or bool(raw.get("recurringEventId"))
    if not recurring:
        return False, None
    freq = ""
    for r in rules or []:
        up = str(r).upper()
        if "FREQ=" in up:
            freq = up.split("FREQ=", 1)[1].split(";", 1)[0].strip()
            break
    label = {
        "DAILY": "daily", "WEEKLY": "weekly", "MONTHLY": "monthly", "YEARLY": "yearly",
    }.get(freq, "recurring")
    return True, label


def _has_conferencing(raw: dict) -> bool:
    # Presence ONLY - we never read or store the join URL.
    return bool(raw.get("conferenceData")) or bool(raw.get("hangoutLink"))


def normalize_event(raw: dict) -> NormalizedEvent | None:
    """Map a raw event to the safe model, or None if it must not be ingested
    (cancelled / private). Reads ONLY allow-list fields; never `description`,
    attachments, join URLs, raw RRULE, attendee emails, or response status."""
    if not is_ingestable(raw):
        return None
    item_id = raw.get("id")
    if not item_id:
        return None

    starts_at, all_day_start = _parse_dt(raw.get("start"))
    ends_at, _ = _parse_dt(raw.get("end"))
    is_recurring, rec_summary = _recurrence_summary(raw)

    organizer = raw.get("organizer") or {}
    attendees = [
        NormalizedAttendee(display=(a.get("displayName") or ""), domain=_domain_of(a.get("email")))
        for a in (raw.get("attendees") or [])
    ]

    return NormalizedEvent(
        calendar_item_id=str(item_id),
        title=(raw.get("summary") or "(no title)"),
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day_start,
        organizer_display=(organizer.get("displayName") or ""),
        organizer_domain=_domain_of(organizer.get("email")),
        is_recurring=is_recurring,
        recurrence_summary=rec_summary,
        has_conferencing=_has_conferencing(raw),
        attendees=attendees,
    )
