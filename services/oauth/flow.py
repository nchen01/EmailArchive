"""Gmail OAuth connect flow (S23 / S20 §2, §5, §8).

Orchestrates start → callback (validate state + PKCE, exchange, verify account,
enforce mismatch rules, store the refresh token in the vault, bind a provider
account with only vault_ref + safe metadata) → disconnect. All audit metadata is
safe scalar-only (ids/provider/reason categories/timestamps); no token, code,
id_token, provider response, or content is ever logged or audited.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from services.db import models as orm

from .config import GMAIL_SCOPES, GmailOAuthConfig
from .gmail_client import GmailOAuthClient
from .pkce import code_challenge, new_code_verifier, new_state
from .vault import TokenVault

STATE_TTL = timedelta(minutes=10)


class OAuthStateError(RuntimeError):
    """Missing/invalid/expired/replayed state (fail closed)."""


class OAuthCallbackError(RuntimeError):
    """Token exchange / verification failure (safe category)."""


class AccountMismatchError(RuntimeError):
    """Connected account does not match the expected owner / is owned elsewhere."""


class ProviderNotConnected(RuntimeError):
    """No connected provider account for the mailbox."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, *, mailbox_id: str | None, actor: str, action: str, scope: str | None) -> None:
    """Best-effort safe audit (ids/action/category only). Never masks the flow."""
    if not mailbox_id:
        return
    try:
        db.add(
            orm.AuditLog(
                mailbox_id=mailbox_id, actor=actor, action=action,
                scope=scope, started_at=_now(),
            )
        )
        db.commit()
    except Exception:
        db.rollback()


def start_connect(db, *, principal, mailbox, oauth_client: GmailOAuthClient, config: GmailOAuthConfig) -> str:
    """Create single-use state (bound to tenant/user/mailbox/provider) + PKCE and
    return the Google authorization URL. Stores no tokens."""
    verifier = new_code_verifier()
    state = new_state()
    db.add(
        orm.OAuthState(
            id=state, tenant_id=principal.tenant_id, user_id=principal.user_id,
            mailbox_id=str(mailbox.id), provider="gmail", code_verifier=verifier,
            expires_at=_now() + STATE_TTL,
        )
    )
    db.commit()
    url = oauth_client.authorization_url(
        state=state, code_challenge=code_challenge(verifier), redirect_uri=config.redirect_uri
    )
    _audit(db, mailbox_id=str(mailbox.id), actor=principal.user_id,
           action="oauth_start_created", scope="gmail")
    return url


def complete_callback(db, *, code: str, state: str, oauth_client: GmailOAuthClient,
                      vault: TokenVault, config: GmailOAuthConfig) -> orm.MailboxProviderAccount:
    st = db.get(orm.OAuthState, state) if state else None
    now = _now()
    if st is None or st.consumed_at is not None or st.expires_at <= now:
        _audit(db, mailbox_id=(st.mailbox_id if st else None),
               actor=(st.user_id if st else "unknown"),
               action="oauth_callback_failed", scope="invalid_or_expired_state")
        raise OAuthStateError("invalid, expired, or already-used state")

    # Atomically claim the state (single-use); a replay matches zero rows.
    claimed = db.execute(
        update(orm.OAuthState)
        .where(orm.OAuthState.id == state, orm.OAuthState.consumed_at.is_(None))
        .values(consumed_at=now)
    ).rowcount
    db.commit()
    if not claimed:
        _audit(db, mailbox_id=st.mailbox_id, actor=st.user_id,
               action="oauth_callback_failed", scope="replayed_state")
        raise OAuthStateError("state already consumed")

    mailbox = db.get(orm.Mailbox, st.mailbox_id)

    try:
        result = oauth_client.exchange_code(
            code=code, code_verifier=st.code_verifier, redirect_uri=config.redirect_uri
        )
    except Exception:
        _audit(db, mailbox_id=st.mailbox_id, actor=st.user_id,
               action="oauth_callback_failed", scope="token_exchange_failed")
        raise OAuthCallbackError("token exchange failed") from None

    if not result.account_email:
        _audit(db, mailbox_id=st.mailbox_id, actor=st.user_id,
               action="oauth_callback_failed", scope="no_verified_email")
        raise OAuthCallbackError("no verified email")

    expected = (getattr(mailbox, "owner_email", "") or "").strip().lower()
    connected = result.account_email.strip().lower()

    # Mismatch rules (S20 §5) — no binding or token is persisted on a mismatch.
    if expected and connected != expected:
        _audit(db, mailbox_id=st.mailbox_id, actor=st.user_id,
               action="oauth_account_mismatch", scope="email_mismatch")
        raise AccountMismatchError("connected account does not match your account")

    if result.account_sub:
        others = db.execute(
            select(orm.MailboxProviderAccount).where(
                orm.MailboxProviderAccount.provider == "gmail",
                orm.MailboxProviderAccount.provider_account_sub == result.account_sub,
                orm.MailboxProviderAccount.status == "connected",
            )
        ).scalars().all()
        for o in others:
            if str(o.owner_user_id) != str(st.user_id) or str(o.tenant_id) != str(st.tenant_id):
                _audit(db, mailbox_id=st.mailbox_id, actor=st.user_id,
                       action="oauth_account_mismatch", scope="cross_owner")
                raise AccountMismatchError("this Google account is connected elsewhere")

    # Store the refresh token ONLY in the vault; the DB gets a vault_ref.
    vault_ref = f"gmail:{st.mailbox_id}:{uuid.uuid4()}"
    vault.store_refresh_token(
        vault_ref, result.refresh_token,
        metadata={"email": result.account_email, "sub": result.account_sub},
    )

    acct = db.execute(
        select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == st.mailbox_id,
            orm.MailboxProviderAccount.tenant_id == st.tenant_id,
            orm.MailboxProviderAccount.provider == "gmail",
        )
    ).scalar_one_or_none()
    if acct is None:
        acct = orm.MailboxProviderAccount(
            tenant_id=st.tenant_id, owner_user_id=st.user_id,
            mailbox_id=st.mailbox_id, provider="gmail",
            provider_account_email=result.account_email,
        )
        db.add(acct)
    elif acct.vault_ref and acct.vault_ref != vault_ref:
        # Reconnect: drop the previous vault entry.
        try:
            vault.revoke(acct.vault_ref)
        except Exception:
            pass

    acct.provider_account_email = result.account_email
    acct.provider_account_sub = result.account_sub or None
    acct.vault_ref = vault_ref
    acct.scopes_granted = list(result.scopes) if result.scopes else list(GMAIL_SCOPES)
    acct.status = "connected"
    acct.connected_at = now
    acct.last_verified_at = now
    acct.disconnected_at = None
    acct.mismatch_reason = None
    acct.expected_account_email = expected or None
    db.commit()

    _audit(db, mailbox_id=st.mailbox_id, actor=st.user_id,
           action="provider_account_connected", scope="gmail")
    _audit(db, mailbox_id=st.mailbox_id, actor=st.user_id,
           action="oauth_callback_succeeded", scope="gmail")
    return acct


def get_connected_account(db, *, tenant_id: str, mailbox_id: str) -> orm.MailboxProviderAccount | None:
    acct = db.execute(
        select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == mailbox_id,
            orm.MailboxProviderAccount.tenant_id == tenant_id,
            orm.MailboxProviderAccount.provider == "gmail",
        )
    ).scalar_one_or_none()
    return acct if (acct and acct.status == "connected") else None


def disconnect_account(db, *, account: orm.MailboxProviderAccount | None, vault: TokenVault) -> bool:
    """Core provider-disconnect transition (S30): provider-side revoke + delete the
    vault entry, then mark the account disconnected and drop the vault_ref. Does NOT
    audit — the caller writes its own audit (owner vs. admin actor). Returns whether
    a live account was disconnected (idempotent no-op → False)."""
    if account is None or account.status not in ("connected", "refresh_failed"):
        return False
    if account.vault_ref:
        try:
            vault.revoke(account.vault_ref)  # provider-side revoke + delete vault entry
        except Exception:
            pass
    account.status = "disconnected"
    account.disconnected_at = _now()
    account.vault_ref = None
    db.commit()
    return True


def disconnect(db, *, principal, mailbox, vault: TokenVault) -> bool:
    acct = db.execute(
        select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == str(mailbox.id),
            orm.MailboxProviderAccount.tenant_id == principal.tenant_id,
            orm.MailboxProviderAccount.provider == "gmail",
        )
    ).scalar_one_or_none()
    if not disconnect_account(db, account=acct, vault=vault):
        return False
    _audit(db, mailbox_id=str(mailbox.id), actor=principal.user_id,
           action="provider_account_disconnected", scope="gmail")
    return True
