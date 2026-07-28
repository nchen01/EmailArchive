"""Gmail OAuth configuration + least-privilege scopes (S23 / S20 §6)."""
from __future__ import annotations

import os
from dataclasses import dataclass

# Least-privilege scopes (S20 §6): identity/email verification + read-only ingest.
# NO send/write/modify scopes.
GMAIL_SCOPES: tuple[str, ...] = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
)

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

_DEFAULT_REDIRECT = "http://127.0.0.1:8000/api/oauth/gmail/callback"


@dataclass(frozen=True)
class GmailOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def load_config() -> GmailOAuthConfig:
    """Read the confidential web-app client from env / the secrets manager.

    The client secret is read here and used only server-side (token exchange); it
    is never sent to the frontend or written to the app DB.
    """
    return GmailOAuthConfig(
        client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
        client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        redirect_uri=os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", _DEFAULT_REDIRECT),
    )
