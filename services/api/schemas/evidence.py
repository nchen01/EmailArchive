"""Source-message detail DTO (S14).

The response of ``GET /api/source-message/{mailbox_id}``. It carries only
citation-safe metadata: subject, date, a bounded snippet, the sender's display
name + registered domain, the provider type, and a best-effort Gmail search URL.

It NEVER carries raw MIME, the full body, OAuth tokens, provider internals, or
any content from a sensitivity-excluded message (the endpoint returns 404 for
those, so a sensitive message cannot even be confirmed to exist).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SourceMessageDetail(BaseModel):
    message_id_header: str
    subject: str
    date: str                         # ISO 8601 timestamp string ("" if unknown)
    sender_display: str
    sender_domain: str
    provider_type: Literal["gmail", "msgraph"]
    snippet: str                      # first 200 chars of clean_text
    source_type: Literal["l1_structured", "l2_retrieval"] | None = None
    # Best-effort Gmail rfc822msgid search link; Gmail-only, else None (D-S14-1).
    open_url: str | None = None
