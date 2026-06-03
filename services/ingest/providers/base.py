"""Provider abstraction (spec 00 §5). Gmail and M365 are interchangeable behind this."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol, runtime_checkable


@dataclass
class MimePart:
    """A decoded MIME part of a raw message."""
    type: str                       # "text/plain", "text/html", "application/pdf", etc.
    bytes: bytes
    charset: str | None = None
    filename: str | None = None


@dataclass
class RawMessage:
    """Provider-agnostic envelope handed to the normalizer."""
    provider_id: str
    provider_thread_id: str
    headers: dict[str, str]                      # case-sensitive as received from provider
    mime_parts: list[MimePart]
    labels: list[str] = field(default_factory=list)
    # Pre-computed attachment refs (fixture only); bypasses byte-hashing in artifacts.py
    precomputed_attachments: list[dict] = field(default_factory=list)


@runtime_checkable
class MailProvider(Protocol):
    def authorize(self, grant: dict) -> None: ...
    def list_ids(self, since_token: str | None) -> Iterator[str]: ...
    def fetch(self, provider_id: str) -> RawMessage: ...
    def fetch_all(self) -> Iterator[RawMessage]: ...
    def sync_token(self) -> str: ...
