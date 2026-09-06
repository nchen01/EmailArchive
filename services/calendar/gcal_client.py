"""Google Calendar API client seam (S51).

A read-only client the sync job calls to list events in a window. The real client
uses the Calendar `events.list` REST endpoint with a short-lived access token
minted from the vault (S23/S50) - it uses ONLY the already-granted
`calendar.events.readonly` scope and never requests `calendar.readonly`. Tests
inject a fake via `set_calendar_client` and never hit Google.

Nothing here logs the access token or raw provider responses.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

_EVENTS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"


class CalendarClientError(RuntimeError):
    """Client-side calendar failure (safe category; never carries token/response)."""


@runtime_checkable
class CalendarClient(Protocol):
    def list_events(
        self, *, access_token: str, calendar_id: str, time_min: str | None,
        time_max: str | None, max_results: int,
    ) -> list[dict]: ...


class GoogleCalendarClient:
    """Real read-only Calendar client (events.list). Used in production."""

    def list_events(
        self, *, access_token: str, calendar_id: str, time_min: str | None,
        time_max: str | None, max_results: int,
    ) -> list[dict]:
        import urllib.parse

        import httpx

        params: dict[str, str] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(max(1, min(int(max_results), 2500))),
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        url = _EVENTS_ENDPOINT.format(cal=urllib.parse.quote(calendar_id, safe=""))
        items: list[dict] = []
        page_token: str | None = None
        with httpx.Client(timeout=30) as client:
            while True:
                q = dict(params)
                if page_token:
                    q["pageToken"] = page_token
                resp = client.get(
                    url, params=q, headers={"Authorization": f"Bearer {access_token}"}
                )
                if resp.status_code != 200:
                    raise CalendarClientError("events_list_failed")  # no body in message
                data = resp.json()
                items.extend(data.get("items", []) or [])
                page_token = data.get("nextPageToken")
                if not page_token or len(items) >= int(max_results):
                    break
        return items[: int(max_results)]


# -- Process registry (swappable for tests) -----------------------------------
_client: CalendarClient | None = None


def get_calendar_client() -> CalendarClient:
    global _client
    if _client is None:
        _client = GoogleCalendarClient()
    return _client


def set_calendar_client(client: CalendarClient | None) -> None:
    global _client
    _client = client
