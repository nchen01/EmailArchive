"""Gmail provider (spec 00 §5). Full implementation behind the MailProvider protocol.

Not exercised in S1 (FixtureProvider is the test path), but written for real:
auth via google-auth credentials, list/fetch via the Gmail REST API, incremental
sync via historyId, exponential backoff on 429.
"""
from __future__ import annotations

import base64
import email
import json
import os
import time
from datetime import timedelta
from email.message import Message as EmailMessage
from typing import Iterator

from ..list_options import ListOptions
from ..params import IngestParams
from .base import MimePart, RawMessage


def build_gmail_query(options: ListOptions | None) -> str | None:
    """Translate a provider-neutral date window into a Gmail ``q=`` search string.

    Returns ``None`` when there is no window (so the caller omits ``q`` entirely
    and lists the whole mailbox, unchanged behavior).

    Gmail's ``after:``/``before:`` operators compare the message's internal
    (received) date at day granularity, in ``YYYY/MM/DD`` form:
      - ``after:D``  matches messages received on or after day D (inclusive).
      - ``before:D`` matches messages received strictly before day D (exclusive).
    So the product-level **inclusive** ``date_to`` is implemented by shifting the
    Gmail ``before:`` bound forward by one day (D-S16.0-1/-3).

    Only validated ``date`` objects are ever formatted into the query — no raw
    user text is interpolated.
    """
    if options is None or not options.is_windowed():
        return None
    parts: list[str] = []
    if options.date_from is not None:
        parts.append(f"after:{options.date_from.strftime('%Y/%m/%d')}")
    if options.date_to is not None:
        upper = options.date_to + timedelta(days=1)  # inclusive date_to → before next day
        parts.append(f"before:{upper.strftime('%Y/%m/%d')}")
    return " ".join(parts) if parts else None


def get_token(mailbox_id: str) -> dict:
    """Retrieve an OAuth token for a mailbox.

    Production wires a secrets manager; S1 reads an env var (decision D6).
    Tokens never touch the app DB or logs.
    """
    token_json = os.environ.get(f"GMAIL_TOKEN_{mailbox_id}") or os.environ.get("GMAIL_TOKEN")
    if not token_json:
        raise RuntimeError(f"No token found for mailbox {mailbox_id}")
    return json.loads(token_json)


class GmailProvider:
    """Gmail-backed MailProvider."""

    def __init__(self, params: IngestParams, mailbox_id: str = ""):
        self.params = params
        self.mailbox_id = mailbox_id
        self._service = None
        self._history_id: str | None = None

    # ── auth ────────────────────────────────────────────────────────────────
    def authorize(self, grant: dict) -> None:
        """Build the Gmail service client.

        ``grant`` may contain either:
        - A raw token dict (passed to ``Credentials(**token)``), or
        - ``{"credentials": <pre-built Credentials object>}`` for callers
          that have already constructed the Credentials (e.g. the smoke runner
          that validates the token dict fields before calling authorize).
        Passing an empty dict falls back to ``get_token(self.mailbox_id)``.
        """
        from google.oauth2.credentials import Credentials  # lazy import
        from googleapiclient.discovery import build

        if isinstance(grant.get("credentials"), Credentials):
            creds = grant["credentials"]
        else:
            token = grant or get_token(self.mailbox_id)
            creds = Credentials(**token)
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _require_service(self):
        if self._service is None:
            raise RuntimeError("GmailProvider.authorize() must be called first")
        return self._service

    def get_profile_email(self) -> str:
        """Return the authenticated account's email (Gmail getProfile).

        Used to verify the OAuth token's account matches the target mailbox
        before any listing/fetch (guards against a token/mailbox mismatch).
        Returns "" if the provider does not report an address.
        """
        svc = self._require_service()
        profile = self._with_backoff(
            lambda: svc.users().getProfile(userId="me").execute()
        )
        return profile.get("emailAddress", "") or ""

    # ── retry helper ────────────────────────────────────────────────────────
    def _with_backoff(self, fn):
        from googleapiclient.errors import HttpError

        backoff = 1.0
        while True:
            try:
                return fn()
            except HttpError as e:  # pragma: no cover - network path
                status = getattr(e, "status_code", None) or getattr(
                    getattr(e, "resp", None), "status", None
                )
                if status == 429 and backoff <= self.params.max_backoff_s:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, self.params.max_backoff_s)
                    continue
                raise

    # ── listing ─────────────────────────────────────────────────────────────
    def list_ids(
        self, since_token: str | None, options: ListOptions | None = None
    ) -> Iterator[str]:
        svc = self._require_service()
        query = build_gmail_query(options)
        # A date window is a scoped snapshot: Gmail history is not date-scoped, so
        # a windowed run must NOT use the incremental history path (D-S16.0-5).
        # The caller (run_ingest / the CLI) also forces since_token=None for
        # windowed runs; guarding here keeps the provider correct on its own.
        if since_token and query is None:
            yield from self._list_incremental(since_token)
            return
        # getProfile reliably returns the mailbox's current historyId before we
        # start fetching, which messages.list does not include in its response.
        profile = self._with_backoff(
            lambda: svc.users().getProfile(userId="me").execute()
        )
        if profile.get("historyId"):
            self._history_id = profile["historyId"]
        page_token = None
        while True:
            list_kwargs = {
                "userId": "me",
                "maxResults": self.params.batch_size,
                "pageToken": page_token,
            }
            if query is not None:
                list_kwargs["q"] = query
            resp = self._with_backoff(
                lambda: svc.users().messages().list(**list_kwargs).execute()
            )
            for m in resp.get("messages", []):
                yield m["id"]
            if "historyId" in resp:
                self._history_id = resp["historyId"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    def _list_incremental(self, since_token: str) -> Iterator[str]:
        svc = self._require_service()
        page_token = None
        while True:
            resp = self._with_backoff(
                lambda: svc.users()
                .history()
                .list(userId="me", startHistoryId=since_token, pageToken=page_token)
                .execute()
            )
            for h in resp.get("history", []):
                for added in h.get("messagesAdded", []):
                    yield added["message"]["id"]
            if "historyId" in resp:
                self._history_id = resp["historyId"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    # ── fetch ───────────────────────────────────────────────────────────────
    def fetch(self, provider_id: str) -> RawMessage:
        svc = self._require_service()
        resp = self._with_backoff(
            lambda: svc.users()
            .messages()
            .get(userId="me", id=provider_id, format="raw")
            .execute()
        )
        raw_bytes = base64.urlsafe_b64decode(resp["raw"].encode("ascii"))
        msg = email.message_from_bytes(raw_bytes)
        return self._parse(provider_id, resp.get("threadId", ""), resp.get("labelIds", []), msg)

    def fetch_all(self) -> Iterator[RawMessage]:
        for pid in self.list_ids(None):
            yield self.fetch(pid)

    def sync_token(self) -> str:
        return self._history_id or ""

    # ── MIME parsing ──────────────────────────────────────────────────────────
    @staticmethod
    def _parse(
        provider_id: str, thread_id: str, labels: list[str], msg: EmailMessage
    ) -> RawMessage:
        headers = {k: v for k, v in msg.items()}
        parts: list[MimePart] = []
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            parts.append(
                MimePart(
                    type=ctype,
                    bytes=payload,
                    charset=part.get_content_charset(),
                    filename=part.get_filename(),
                )
            )
        return RawMessage(
            provider_id=provider_id,
            provider_thread_id=thread_id,
            headers=headers,
            mime_parts=parts,
            labels=list(labels),
        )
