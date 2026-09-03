"""Gmail OAuth connect endpoints (S23 — implements docs/s20-oauth-token-vault-plan.md).

  POST /api/mailbox/{mailbox_id}/gmail/connect/start   creator-authed → auth URL
  GET  /api/oauth/gmail/callback                        Google redirect (state-authed)
  POST /api/mailbox/{mailbox_id}/gmail/disconnect       creator-authed → revoke
  GET  /api/mailbox/{mailbox_id}/gmail/status           creator-authed → safe status

The app DB stores only a vault_ref + safe provider metadata; raw tokens live only
in the token vault. Recipient routes are untouched and never use any of this.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from services.db import models as orm
from services.oauth import flow
from services.oauth.config import load_config
from services.oauth.gmail_client import OAuthClientError, get_oauth_client
from services.oauth.vault import VaultError, get_vault

from ..auth import Principal, get_principal, require_owner_mailbox
from ..deps import get_db

router = APIRouter(tags=["oauth-gmail"])

# Where the callback sends the browser back to (safe query param only, no tokens).
_RESULT_PATH = "/app/status"


class StartConnectResponse(BaseModel):
    authorization_url: str


class GmailStatusResponse(BaseModel):
    connected: bool
    provider_account_email: str | None = None
    provider_account_sub: str | None = None
    scopes_granted: list[str] = []
    status: str | None = None
    connected_at: str | None = None
    last_verified_at: str | None = None


class DisconnectResponse(BaseModel):
    disconnected: bool


@router.post(
    "/mailbox/{mailbox_id}/gmail/connect/start",
    response_model=StartConnectResponse,
)
async def start_connect(
    mailbox_id: str,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> StartConnectResponse:
    try:
        url = flow.start_connect(
            db, principal=principal, mailbox=mbx,
            oauth_client=get_oauth_client(), config=load_config(),
        )
    except OAuthClientError:
        raise HTTPException(status_code=503, detail="Gmail OAuth is not configured.") from None
    return StartConnectResponse(authorization_url=url)


@router.get("/oauth/gmail/callback")
async def gmail_callback(
    state: str = Query(""),
    code: str = Query(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Google's redirect target. State-authed (no principal); validates + binds."""
    def redirect(result: str) -> RedirectResponse:
        return RedirectResponse(url=f"{_RESULT_PATH}?gmail_connect={result}", status_code=302)

    try:
        vault = get_vault()
    except VaultError:
        return redirect("vault_unavailable")
    try:
        flow.complete_callback(
            db, code=code, state=state,
            oauth_client=get_oauth_client(), vault=vault, config=load_config(),
            expected_provider="gmail",
        )
    except flow.OAuthStateError:
        return redirect("invalid_state")
    except flow.AccountMismatchError:
        return redirect("account_mismatch")
    except (flow.OAuthCallbackError, OAuthClientError):
        return redirect("exchange_failed")
    return redirect("connected")


@router.post(
    "/mailbox/{mailbox_id}/gmail/disconnect",
    response_model=DisconnectResponse,
)
async def disconnect(
    mailbox_id: str,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> DisconnectResponse:
    # Fail closed (S30 hardening): require a real vault and a successful provider-side
    # revoke before marking the account disconnected. If the vault is unavailable or
    # the revoke fails, return 503 and leave status/vault_ref unchanged — never orphan
    # a live token by clearing the ref without revoking it.
    try:
        vault = get_vault()
    except VaultError:
        raise HTTPException(status_code=503, detail="Provider vault unavailable.")
    try:
        done = flow.disconnect(db, principal=principal, mailbox=mbx, vault=vault)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=503, detail="Provider disconnect failed.")
    return DisconnectResponse(disconnected=done)


@router.get(
    "/mailbox/{mailbox_id}/gmail/status",
    response_model=GmailStatusResponse,
)
async def gmail_status(
    mailbox_id: str,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> GmailStatusResponse:
    acct = flow.get_connected_account(db, tenant_id=principal.tenant_id, mailbox_id=str(mbx.id))
    if acct is None:
        return GmailStatusResponse(connected=False)
    return GmailStatusResponse(
        connected=True,
        provider_account_email=acct.provider_account_email,
        provider_account_sub=acct.provider_account_sub,
        scopes_granted=list(acct.scopes_granted or []),
        status=acct.status,
        connected_at=acct.connected_at.isoformat() if acct.connected_at else None,
        last_verified_at=acct.last_verified_at.isoformat() if acct.last_verified_at else None,
    )
