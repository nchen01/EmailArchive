"""Gmail OAuth client seam (S23 / S20 §2, §6).

A provider client the flow calls to build the auth URL, exchange the code, refresh,
and revoke. ``GoogleGmailOAuthClient`` is the real (httpx) implementation used in
production; tests inject a fake via ``set_oauth_client`` and never hit Google.

Nothing here logs the code, tokens, id_token, or raw provider responses.
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .config import (
    GMAIL_SCOPES,
    GOOGLE_AUTH_ENDPOINT,
    GOOGLE_REVOKE_ENDPOINT,
    GOOGLE_TOKEN_ENDPOINT,
    GmailOAuthConfig,
    load_config,
)

_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"


class OAuthClientError(RuntimeError):
    """Client-side OAuth failure (safe category, never carries token/response body)."""


@dataclass
class TokenExchangeResult:
    refresh_token: str
    access_token: str
    account_email: str
    account_sub: str
    scopes: list[str] = field(default_factory=list)


@runtime_checkable
class GmailOAuthClient(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...
    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenExchangeResult: ...
    def refresh_access_token(self, refresh_token: str) -> str: ...
    def revoke(self, refresh_token: str) -> None: ...


class GoogleGmailOAuthClient:
    """Real Google OAuth client (confidential web app). Used in production.

    Scope-parameterized (S50): defaults to `GMAIL_SCOPES` so existing Gmail
    behavior is byte-identical, but the calendar registry constructs the SAME
    client with `CALENDAR_SCOPES`. The token-exchange / userinfo / refresh / revoke
    logic is provider-agnostic (all Google endpoints), so only the requested scopes
    differ."""

    def __init__(
        self, config: GmailOAuthConfig | None = None, *, scopes: tuple[str, ...] = GMAIL_SCOPES
    ) -> None:
        self.config = config or load_config()
        self.scopes = tuple(scopes)

    def _require_configured(self) -> None:
        if not self.config.configured:
            raise OAuthClientError(
                "Google OAuth is not configured (set GOOGLE_OAUTH_CLIENT_ID / "
                "GOOGLE_OAUTH_CLIENT_SECRET)."
            )

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        self._require_configured()
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",  # obtain a refresh token
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return f"{GOOGLE_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenExchangeResult:
        self._require_configured()
        import httpx

        with httpx.Client(timeout=20) as client:
            resp = client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if resp.status_code != 200:
                raise OAuthClientError("token_exchange_failed")  # no body in message
            tok = resp.json()
            access = tok.get("access_token")
            refresh = tok.get("refresh_token")
            granted = (tok.get("scope") or "").split()
            if not access or not refresh:
                raise OAuthClientError("token_exchange_incomplete")
            # Verify the connected account via the userinfo endpoint.
            ui = client.get(_USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access}"})
            if ui.status_code != 200:
                raise OAuthClientError("userinfo_failed")
            info = ui.json()
        email = info.get("email")
        if not email or info.get("email_verified") is False:
            raise OAuthClientError("no_verified_email")
        return TokenExchangeResult(
            refresh_token=refresh, access_token=access,
            account_email=email, account_sub=info.get("sub", ""), scopes=granted,
        )

    def refresh_access_token(self, refresh_token: str) -> str:
        self._require_configured()
        import httpx

        with httpx.Client(timeout=20) as client:
            resp = client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if resp.status_code != 200:
            raise OAuthClientError("token_refresh_failed")
        access = resp.json().get("access_token")
        if not access:
            raise OAuthClientError("token_refresh_incomplete")
        return access

    def revoke(self, refresh_token: str) -> None:
        import httpx

        with httpx.Client(timeout=20) as client:
            client.post(GOOGLE_REVOKE_ENDPOINT, data={"token": refresh_token})


# ── Process registry (swappable for tests) ───────────────────────────────────
_client: GmailOAuthClient | None = None


def get_oauth_client() -> GmailOAuthClient:
    global _client
    if _client is None:
        _client = GoogleGmailOAuthClient()
    return _client


def set_oauth_client(client: GmailOAuthClient | None) -> None:
    global _client
    _client = client


# -- Calendar client registry (S50) - a distinct, calendar-scoped Google client.
# Kept separate from the Gmail registry so a test/prod can swap one without the
# other, and so the calendar flow requests ONLY the calendar scopes.
_calendar_client: GmailOAuthClient | None = None


def get_calendar_oauth_client() -> GmailOAuthClient:
    global _calendar_client
    if _calendar_client is None:
        from .config import CALENDAR_SCOPES, load_calendar_config
        _calendar_client = GoogleGmailOAuthClient(
            config=load_calendar_config(), scopes=CALENDAR_SCOPES
        )
    return _calendar_client


def set_calendar_oauth_client(client: GmailOAuthClient | None) -> None:
    global _calendar_client
    _calendar_client = client
