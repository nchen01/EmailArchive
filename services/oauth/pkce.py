"""PKCE + state helpers (S23 / S20 §2). Ephemeral per-flow values — never tokens."""
from __future__ import annotations

import base64
import hashlib
import secrets


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_state() -> str:
    """Opaque, unguessable, single-use CSRF state token."""
    return _b64url(secrets.token_bytes(32))


def new_code_verifier() -> str:
    """PKCE code_verifier (RFC 7636): 43–128 chars of unreserved characters."""
    return _b64url(secrets.token_bytes(64))


def code_challenge(verifier: str) -> str:
    """S256 PKCE challenge for a verifier."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
