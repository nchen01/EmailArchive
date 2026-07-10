"""Shared evidence helpers (S14 — evidence & source-navigation polish).

Two provider-honest helpers used by both the Cover-for-me supporting_evidence
builder and the standalone source-message detail endpoint:

- ``gmail_search_url`` — a *best-effort* Gmail deep link built from the RFC
  ``Message-ID`` we already store (``rfc822msgid:`` search). It is Gmail-only and
  account-index agnostic (no hardcoded ``/u/0/``); it opens a Gmail search in
  whatever account the operator is signed into, so the UI labels it "Search in
  Gmail", never "open exact email" (D-S14-1). We never build ``provider_id``
  links. Returns ``None`` for any non-Gmail provider.
- ``sender_fields`` — the sender display name + email domain (the raw part after
  ``@``, not a tldextract registered domain), using only information the
  workspace already surfaces elsewhere (Network Map). Never a richer identity
  than that.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.orm import Session

# The single safe-source predicate, shared by GET /api/source-message and the
# Cover-for-me supporting_evidence builder so neither can drift from the other.
# A message is returnable only when it is itself {none} AND no sibling in its
# thread carries a non-{none} sensitivity tag (whole-thread exclusion, like
# S9/S13). Missing / wrong-mailbox / sensitive headers simply do not come back.
_SAFE_SOURCE_SQL = text(
    """
    SELECT m.message_id_header, m.subject, m.ts, m.clean_text,
           m.sender_email, m.addresses
    FROM message m
    WHERE m.mailbox_id = :mid
      AND m.message_id_header = ANY(:headers)
      AND m.sensitivity = '{none}'
      AND NOT EXISTS (
          SELECT 1 FROM message m2
          WHERE m2.thread_id = m.thread_id
            AND m2.mailbox_id = :mid
            AND m2.sensitivity != '{none}'
      )
    """
)


def fetch_safe_source_rows(
    db: Session, mailbox_id: str, headers: Iterable[str]
) -> dict[str, Mapping]:
    """Return ``{message_id_header: row}`` for the headers that pass the
    whole-thread sensitivity gate.

    Headers that are missing, belong to another mailbox, are directly sensitive,
    or sit in a thread with any sensitive sibling are omitted from the result —
    callers must treat an absent header as "no safe detail available" and never
    emit snippet/sender/link fields for it. This is the one place the predicate
    lives; do not re-inline it with a weaker WHERE clause.
    """
    header_list = list(dict.fromkeys(headers))  # de-dup, preserve order
    if not header_list:
        return {}
    rows = db.execute(
        _SAFE_SOURCE_SQL, {"mid": mailbox_id, "headers": header_list}
    ).mappings().all()
    return {r["message_id_header"]: r for r in rows}


def gmail_search_url(provider: str | None, message_id_header: str | None) -> str | None:
    """Best-effort Gmail ``rfc822msgid:`` search URL, or ``None``.

    Only produced for ``provider == "gmail"``. The Message-ID is stored without
    angle brackets (``norm_mid`` strips them), but we strip defensively in case a
    real ingest ever stores them, then URL-encode the whole value (it contains
    ``@`` and possibly other search-significant characters).
    """
    if provider != "gmail" or not message_id_header:
        return None
    mid = message_id_header.strip().strip("<>")
    if not mid:
        return None
    return f"https://mail.google.com/mail/#search/rfc822msgid:{quote(mid, safe='')}"


def sender_fields(sender_email: str | None, addresses: object) -> tuple[str, str]:
    """Return ``(sender_display, sender_domain)`` from workspace-safe fields.

    ``sender_display`` prefers the parsed display name (``addresses.sender.
    display_names[0]``) and falls back to the address local-part; it never
    invents a name. ``sender_domain`` is the sender's email domain — the part
    after ``@`` (already lowercased by ingest); it is NOT a tldextract
    registered domain. Both are empty strings when unavailable rather than
    ``None`` so the DTO stays simple.
    """
    email = (sender_email or "").strip()
    domain = email.split("@", 1)[1] if "@" in email else ""

    display = ""
    parsed = addresses
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (ValueError, TypeError):
            parsed = None
    if isinstance(parsed, dict):
        sender = parsed.get("sender") or {}
        if isinstance(sender, dict):
            names = sender.get("display_names") or []
            if names and isinstance(names[0], str):
                display = names[0]
    if not display:
        display = email.split("@", 1)[0] if "@" in email else email
    return display, domain
