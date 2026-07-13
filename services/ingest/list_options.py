"""Provider-neutral listing options for date-windowed ingest (S16.0).

`ListOptions` carries source-selection constraints (a date window) that are
applied at *provider listing* time — before any raw message body is fetched.
It is deliberately separate from `IngestParams` (which is normalization/runtime
behavior): a date window is source selection, closer to `IngestConfig.since_token`
and `max_messages` (see docs/s16-date-range-ingest-plan.md, D-S16.0-2).

Date semantics (locked product decisions):
- `date_from` is the **inclusive** start date; `date_to` is the **inclusive**
  end date (a human asking for `2026-06-30` expects June 30 included).
- A missing bound means open-ended on that side.
- Filtering is on the provider's received/internal date, not the email `Date`
  header.

This module also holds the shared validation used by BOTH the CLI and the demo
backend endpoint, so the two cannot drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


class DateWindowError(ValueError):
    """Raised for a malformed date or an inverted (from > to) window.

    Callers surface the message to the operator/user; it never contains provider
    data — only the offending date string(s).
    """


@dataclass(frozen=True)
class ListOptions:
    """Provider-neutral listing constraints. All fields optional (open-ended)."""
    date_from: date | None = None
    date_to: date | None = None

    def is_windowed(self) -> bool:
        """True when at least one date bound is set (i.e. a scoped snapshot)."""
        return self.date_from is not None or self.date_to is not None


def parse_date(value: str | None) -> date | None:
    """Parse a ``YYYY-MM-DD`` string to a ``date``; ``None``/empty → ``None``.

    Raises :class:`DateWindowError` on any non-empty value that is not an exact
    ``YYYY-MM-DD`` calendar date (e.g. ``2026-13-40``, ``last-week``, ``4/1/26``).
    """
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise DateWindowError(
            f"invalid date {value!r}: expected calendar date in YYYY-MM-DD format"
        ) from exc


def parse_date_window(
    date_from: str | None, date_to: str | None
) -> ListOptions:
    """Validate a raw (from, to) string pair into a :class:`ListOptions`.

    Fails (raises :class:`DateWindowError`) on a malformed date or an inverted
    window (``date_from > date_to``) — before any provider/API/DB call. This is
    the single validation both the CLI and the demo endpoint call.
    """
    df = parse_date(date_from)
    dt = parse_date(date_to)
    if df is not None and dt is not None and df > dt:
        raise DateWindowError(
            f"date_from ({df.isoformat()}) is after date_to ({dt.isoformat()}); "
            "the start of the window must not be later than its end"
        )
    return ListOptions(date_from=df, date_to=dt)
