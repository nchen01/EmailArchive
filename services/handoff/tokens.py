"""Recipient capability-code & session-token handling (S17.5).

The recipient never authenticates with a mailbox account. Instead:

- On *publish*, a one-time **capability code** is generated. Only its sha256
  hash is stored (`handoff_recipient.capability_code_hash`); the raw code is
  returned once to the creator to place in the share-link URL **fragment**.
- On *session exchange*, the recipient POSTs the raw code; we hash it, look up
  the recipient, and (if the package is live) issue a short-lived **session
  token**. Again only the sha256 hash is stored.

Raw tokens are therefore never persisted, never logged, and never placed in a
URL path/query. The audit actor for a recipient is derived from the *hash*
prefix, so no raw secret reaches the audit trail either.
"""
from __future__ import annotations

import hashlib
import secrets

# 32 bytes of entropy → ~43 url-safe chars. Ample against guessing.
_TOKEN_BYTES = 32
# Short-lived recipient session. Access control also re-checks the package at
# request time, so this is a defence-in-depth ceiling, not the only gate.
SESSION_TTL_MINUTES = 60


def new_capability_code() -> str:
    """A fresh one-time capability code (raw; never stored, shared via fragment)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def new_session_token() -> str:
    """A fresh short-lived recipient session bearer token (raw; never stored)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(raw: str) -> str:
    """Stable sha256 hex of a raw token — this is what we persist and look up by."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def actor_hash_prefix(token_hash: str) -> str:
    """Short, non-reversible recipient identifier for audit rows.

    Derived from the stored *hash* (never the raw token), so it is safe to write
    into `handoff_audit_event.actor` as ``recipient:<prefix>``.
    """
    return token_hash[:12]
