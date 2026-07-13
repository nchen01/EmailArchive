"""Dev/test provider: yields RawMessage from fixtures/mailbox.json (decision D4).

Wraps each message's `body_text` as a single text/plain MIME part so body
normalization actually runs. Surfaces pre-computed attachment sha256s directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from ..list_options import ListOptions
from .base import MailProvider, MimePart, RawMessage


class FixtureProvider:
    """Implements the MailProvider protocol against a JSON fixture mailbox."""

    def __init__(self, mailbox_path: str | Path | None):
        if mailbox_path is None:
            raise ValueError("FixtureProvider requires a mailbox_path")
        self._path = Path(mailbox_path)
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self.owner_email: str = data.get("owner_email", "")
        self.internal_domains: list[str] = data.get("internal_domains", [])
        self._messages: list[dict] = data.get("messages", [])

    def authorize(self, grant: dict) -> None:  # no-op for the fixture
        return None

    def list_ids(
        self, since_token: str | None, options: ListOptions | None = None
    ) -> Iterator[str]:
        # The fixture is not date-windowed source data; date options are accepted
        # for protocol compatibility and ignored (documented no-op, D-S16.0-2).
        for m in self._messages:
            yield m["provider_id"]

    def fetch(self, provider_id: str) -> RawMessage:
        for m in self._messages:
            if m["provider_id"] == provider_id:
                return self._to_raw(m)
        raise KeyError(f"provider_id not found in fixture: {provider_id}")

    def fetch_all(self) -> Iterator[RawMessage]:
        for m in self._messages:
            yield self._to_raw(m)

    def sync_token(self) -> str:
        return "fixture"

    @staticmethod
    def _to_raw(m: dict) -> RawMessage:
        body_text = m.get("body_text", "") or ""
        part = MimePart(
            type="text/plain",
            bytes=body_text.encode("utf-8"),
            charset="utf-8",
        )
        return RawMessage(
            provider_id=m["provider_id"],
            provider_thread_id=m.get("provider_thread_id", ""),
            headers=dict(m.get("headers", {})),
            mime_parts=[part],
            labels=list(m.get("labels", [])),
            precomputed_attachments=list(m.get("attachments", [])),
        )


# Static type sanity: FixtureProvider satisfies MailProvider.
_: type[MailProvider] = FixtureProvider  # noqa: F841
