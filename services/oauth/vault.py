"""Token vault seam (S23 / S20 §3).

**Hard rule:** raw OAuth refresh/access tokens live ONLY inside the vault — never
the app DB, logs, audit metadata, API responses, or frontend state. The app DB
stores only a ``vault_ref`` handle + safe provider metadata.

S23 ships a **DEV/TEST-ONLY** vault (`DevTokenVault`): refresh tokens are
Fernet-encrypted and held in an isolated in-process store. It refuses to run
unless ``AUTH_MODE=dev`` or ``EKC_ALLOW_DEV_VAULT=1``. A production deployment
MUST supply a KMS / secrets-manager-backed ``TokenVault`` (a later sprint); the
provider-neutral interface below is what it plugs into. The refresh token never
leaves the vault: refresh (mint a short-lived access token) and provider-side
revoke happen INSIDE the vault via callables injected from the OAuth client.
"""
from __future__ import annotations

import os
from typing import Callable, Protocol, runtime_checkable

from cryptography.fernet import Fernet


class VaultError(RuntimeError):
    pass


class VaultKeyError(VaultError):
    """Requested vault_ref is not present (e.g. already revoked)."""


@runtime_checkable
class TokenVault(Protocol):
    def store_refresh_token(
        self, vault_ref: str, refresh_token: str, *, metadata: dict | None = None
    ) -> None: ...
    def get_access_token(self, vault_ref: str) -> str: ...
    def revoke(self, vault_ref: str) -> None: ...
    def get_metadata(self, vault_ref: str) -> dict | None: ...
    def exists(self, vault_ref: str) -> bool: ...


def _dev_vault_allowed() -> bool:
    return (
        os.environ.get("AUTH_MODE", "").strip().lower() == "dev"
        or os.environ.get("EKC_ALLOW_DEV_VAULT", "").strip() == "1"
    )


class DevTokenVault:
    """DEV/TEST-ONLY in-process encrypted token vault (see module docstring)."""

    def __init__(
        self,
        *,
        refresher: Callable[[str], str] | None = None,
        revoker: Callable[[str], None] | None = None,
        key: bytes | None = None,
    ) -> None:
        if not _dev_vault_allowed():
            raise VaultError(
                "DevTokenVault is dev/test-only; set AUTH_MODE=dev or "
                "EKC_ALLOW_DEV_VAULT=1, or supply a production TokenVault."
            )
        self._fernet = Fernet(key or Fernet.generate_key())
        self._refresher = refresher
        self._revoker = revoker
        # vault_ref -> (encrypted_refresh_token, safe_metadata)
        self._store: dict[str, tuple[bytes, dict]] = {}

    def store_refresh_token(
        self, vault_ref: str, refresh_token: str, *, metadata: dict | None = None
    ) -> None:
        self._store[vault_ref] = (
            self._fernet.encrypt(refresh_token.encode("utf-8")),
            dict(metadata or {}),
        )

    def _decrypt(self, vault_ref: str) -> str:
        rec = self._store.get(vault_ref)
        if rec is None:
            raise VaultKeyError(vault_ref)
        return self._fernet.decrypt(rec[0]).decode("utf-8")

    def get_access_token(self, vault_ref: str) -> str:
        """Mint a short-lived access token by refreshing the stored refresh token.
        The refresh token never leaves this method."""
        refresh_token = self._decrypt(vault_ref)
        if self._refresher is None:
            raise VaultError("vault has no refresher (OAuth client not wired)")
        return self._refresher(refresh_token)

    def revoke(self, vault_ref: str) -> None:
        rec = self._store.get(vault_ref)
        if rec is not None and self._revoker is not None:
            try:
                self._revoker(self._fernet.decrypt(rec[0]).decode("utf-8"))
            except Exception:
                # Provider-side revoke is best-effort; always drop the local entry.
                pass
        self._store.pop(vault_ref, None)

    def get_metadata(self, vault_ref: str) -> dict | None:
        rec = self._store.get(vault_ref)
        return dict(rec[1]) if rec is not None else None

    def exists(self, vault_ref: str) -> bool:
        return vault_ref in self._store


# ── Process registry (swappable for tests / future production vault) ─────────
_vault: TokenVault | None = None


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        from .gmail_client import get_oauth_client

        client = get_oauth_client()
        _vault = DevTokenVault(
            refresher=client.refresh_access_token, revoker=client.revoke
        )
    return _vault


def set_vault(vault: TokenVault | None) -> None:
    global _vault
    _vault = vault


def reset_vault() -> None:
    set_vault(None)


def current_vault_is_dev() -> bool:
    """True when no production TokenVault is registered — i.e. a ``DevTokenVault``
    is (or would be) used. Read by the S27 hosted-readiness guard: hosted production
    must register a real vault via ``set_vault`` so this returns False. A ``None``
    registry counts as dev because ``get_vault`` would lazily build a DevTokenVault."""
    return _vault is None or isinstance(_vault, DevTokenVault)
