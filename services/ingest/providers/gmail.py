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
from email.message import Message as EmailMessage
from typing import Iterator

from ..params import IngestParams
from .base import MimePart, RawMessage


def get_token(mailbox_id: str) -> dict:
    """Retrieve an OAuth token for a mailbox.

    Production wires a secrets manager; S1 reads an env var (decision D6).
    Tokens never touch the app DB or logs.
    """
    token_json = os.environ.get(f"GMAIL_TOKEN_{mailbox_id}")
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
        from google.oauth2.credentials import Credentials  # lazy import
        from googleapiclient.discovery import build

        token = grant or get_token(self.mailbox_id)
        creds = Credentials(**token)
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _require_service(self):
        if self._service is None:
            raise RuntimeError("GmailProvider.authorize() must be called first")
        return self._service

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
    def list_ids(self, since_token: str | None) -> Iterator[str]:
        svc = self._require_service()
        if since_token:
            yield from self._list_incremental(since_token)
            return
        page_token = None
        while True:
            resp = self._with_backoff(
                lambda: svc.users()
                .messages()
                .list(userId="me", maxResults=self.params.batch_size, pageToken=page_token)
                .execute()
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
