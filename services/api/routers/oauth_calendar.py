"""Google Calendar OAuth connect endpoints (S50 - implements docs/s49 section 8).

  POST /api/mailbox/{mailbox_id}/calendar/connect/start   creator-authed -> auth URL
  GET  /api/oauth/calendar/callback                        Google redirect (state-authed)
  POST /api/mailbox/{mailbox_id}/calendar/disconnect       creator-authed -> revoke
  GET  /api/mailbox/{mailbox_id}/calendar/status           creator-authed -> safe status

This is the OAuth/vault/account boundary ONLY - S50 does NOT fetch or store any
calendar events (calendar_event / handoff_calendar_item are deferred to S51/S52).
It reuses the shipped S23 flow (state + PKCE, single-use state, owner/tenant/
mailbox/provider binding, fail-closed callback, refresh token only in the vault)
with provider = 'google_calendar' and a calendar-scoped OAuth client. The app DB
stores only a vault_ref + safe provider metadata; raw tokens live only in the
vault. Recipient routes are untouched and never use any of this.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from services.db import models as orm
from services.oauth import flow
from services.oauth.config import load_calendar_config
from services.oauth.gmail_client import OAuthClientError, get_calendar_oauth_client
from services.oauth.vault import VaultError, get_vault

from ..auth import Principal, get_principal, require_owner_mailbox
from ..deps import get_db

router = APIRouter(tags=["oauth-calendar"])

_PROVIDER = "google_calendar"
# Where the callback sends the browser back to (safe query param only, no tokens).
_RESULT_PATH = "/app/status"


class StartConnectResponse(BaseModel):
    authorization_url: str


class CalendarStatusResponse(BaseModel):
    # Safe metadata ONLY (docs/s49 requirement 8): provider, status, connected
    # account label (email), scopes_granted, and lifecycle timestamps. NEVER
    # vault_ref, token, code, client secret, code_verifier, state, provider raw
    # response, or calendar event data.
    provider: str = _PROVIDER
    connected: bool
    provider_account_email: str | None = None
    scopes_granted: list[str] = []
    status: str | None = None
    connected_at: str | None = None
    last_verified_at: str | None = None
    disconnected_at: str | None = None


class DisconnectResponse(BaseModel):
    disconnected: bool


@router.post(
    "/mailbox/{mailbox_id}/calendar/connect/start",
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
            oauth_client=get_calendar_oauth_client(), config=load_calendar_config(),
            provider=_PROVIDER,
        )
    except OAuthClientError:
        raise HTTPException(status_code=503, detail="Google Calendar OAuth is not configured.") from None
    return StartConnectResponse(authorization_url=url)


@router.get("/oauth/calendar/callback")
async def calendar_callback(
    state: str = Query(""),
    code: str = Query(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Google's redirect target. State-authed (no principal); validates + binds.
    The provider is read from the single-use state row (bound at start), so this
    only ever touches the google_calendar provider account."""
    def redirect(result: str) -> RedirectResponse:
        return RedirectResponse(url=f"{_RESULT_PATH}?calendar_connect={result}", status_code=302)

    try:
        vault = get_vault()
    except VaultError:
        return redirect("vault_unavailable")
    try:
        flow.complete_callback(
            db, code=code, state=state,
            oauth_client=get_calendar_oauth_client(), vault=vault, config=load_calendar_config(),
            expected_provider=_PROVIDER,
        )
    except flow.OAuthStateError:
        return redirect("invalid_state")
    except flow.AccountMismatchError:
        return redirect("account_mismatch")
    except (flow.OAuthCallbackError, OAuthClientError):
        return redirect("exchange_failed")
    return redirect("connected")


@router.post(
    "/mailbox/{mailbox_id}/calendar/disconnect",
    response_model=DisconnectResponse,
)
async def disconnect(
    mailbox_id: str,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> DisconnectResponse:
    # Fail closed (S30 semantics): require a real vault and a successful provider-side
    # revoke before marking the account disconnected. If the vault is unavailable or
    # the revoke fails, return 503 and leave status/vault_ref unchanged - never orphan
    # a live token by clearing the ref without revoking it.
    try:
        vault = get_vault()
    except VaultError:
        raise HTTPException(status_code=503, detail="Provider vault unavailable.")
    try:
        done = flow.disconnect(db, principal=principal, mailbox=mbx, vault=vault, provider=_PROVIDER)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=503, detail="Provider disconnect failed.")
    return DisconnectResponse(disconnected=done)


@router.get(
    "/mailbox/{mailbox_id}/calendar/status",
    response_model=CalendarStatusResponse,
)
async def calendar_status(
    mailbox_id: str,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> CalendarStatusResponse:
    acct = flow.get_account(
        db, tenant_id=principal.tenant_id, mailbox_id=str(mbx.id), provider=_PROVIDER
    )
    if acct is None:
        return CalendarStatusResponse(connected=False)
    return CalendarStatusResponse(
        connected=acct.status == "connected",
        provider_account_email=acct.provider_account_email,
        scopes_granted=list(acct.scopes_granted or []),
        status=acct.status,
        connected_at=acct.connected_at.isoformat() if acct.connected_at else None,
        last_verified_at=acct.last_verified_at.isoformat() if acct.last_verified_at else None,
        disconnected_at=acct.disconnected_at.isoformat() if acct.disconnected_at else None,
    )
