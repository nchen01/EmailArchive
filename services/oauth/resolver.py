"""Vault-backed Gmail credential resolver (S23 / S20 §3, requirement 5).

Replaces the D6 env-var token path when a connected provider account exists. This
is the seam GmailProvider will call; S23 does NOT rewrite ingest — it only adds
the resolver + a fail-closed production path.

  - AUTH_MODE=dev  : keep the existing local env token (D6) so demos still work;
    fall through to the vault only if no env token is present.
  - production      : require a connected provider account and mint a short-lived
    access token via the vault (never a stored token). No account → fail closed.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from services.db import models as orm

from . import flow
from .vault import TokenVault, get_vault


def resolve_gmail_grant(db: Session, mailbox_id: str, *, vault: TokenVault | None = None) -> dict:
    """Return a grant dict for ``GmailProvider.authorize`` (``{"token": access}``)."""
    from services.api.auth import get_auth_mode  # lazy: avoid import cycle

    if get_auth_mode() == "dev":
        from services.ingest.providers.gmail import get_token
        try:
            return get_token(mailbox_id)  # existing local/demo path (D6)
        except RuntimeError:
            pass  # no env token — fall through to a connected account if present

    mbx = db.get(orm.Mailbox, mailbox_id)
    acct = None
    if mbx is not None and mbx.tenant_id is not None:
        acct = flow.get_connected_account(db, tenant_id=str(mbx.tenant_id), mailbox_id=mailbox_id)
    if acct is None or not acct.vault_ref:
        raise flow.ProviderNotConnected(
            f"No connected Gmail account for mailbox {mailbox_id}"
        )
    access_token = (vault or get_vault()).get_access_token(acct.vault_ref)
    return {"token": access_token}


def resolve_calendar_grant(db: Session, mailbox_id: str, *, vault: TokenVault | None = None) -> dict:
    """Return a grant dict (``{"token": access}``) for the connected google_calendar
    account (S51). The refresh token never leaves the vault: a short-lived access
    token is minted from the account's ``vault_ref``. Fail closed if no calendar
    account is connected. Unlike Gmail there is NO dev env-token path - calendar
    always resolves through the vault-backed provider account (tests inject a vault
    + fake client), so a raw token is never read from the environment or the DB."""
    mbx = db.get(orm.Mailbox, mailbox_id)
    acct = None
    if mbx is not None and mbx.tenant_id is not None:
        acct = flow.get_connected_account(
            db, tenant_id=str(mbx.tenant_id), mailbox_id=mailbox_id, provider="google_calendar"
        )
    if acct is None or not acct.vault_ref:
        raise flow.ProviderNotConnected(
            f"No connected Google Calendar account for mailbox {mailbox_id}"
        )
    access_token = (vault or get_vault()).get_access_token(acct.vault_ref)
    return {"token": access_token, "provider_account_id": str(acct.id)}
