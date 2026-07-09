"""Shared evidence helpers (S14 — evidence & source-navigation polish).

Two provider-honest helpers used by both the Cover-for-me supporting_evidence
builder and the standalone source-message detail endpoint:

- ``gmail_search_url`` — a *best-effort* Gmail deep link built from the RFC
  ``Message-ID`` we already store (``rfc822msgid:`` search). It is Gmail-only and
  account-index agnostic (no hardcoded ``/u/0/``); it opens a Gmail search in
  whatever account the operator is signed into, so the UI labels it "Search in
  Gmail", never "open exact email" (D-S14-1). We never build ``provider_id``
  links. Returns ``None`` for any non-Gmail provider.
- ``sender_fields`` — the sender display name + registered domain, using only
  information the workspace already surfaces elsewhere (Network Map). Never a
  richer identity than that.
"""
from __future__ import annotations

import json
from urllib.parse import quote


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
    invents a name. ``sender_domain`` is the part after ``@`` (lowercased by
    ingest already). Both are empty strings when unavailable rather than ``None``
    so the DTO stays simple.
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
