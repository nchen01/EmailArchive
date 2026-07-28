"""S23 — Gmail OAuth + token vault (DB-gated).

Implements docs/s20-oauth-token-vault-plan.md. Verifies the connect flow, the
state+PKCE/mismatch guards, the token-vault boundary (DB stores only vault_ref +
safe metadata; no raw tokens anywhere), disconnect/revoke, the production
vault-backed resolver, the dev env-token fallback, and that recipient routes are
untouched. No real Google calls — a fake OAuth client is injected.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")


def _db_reachable() -> bool:
    if not DATABASE_URL:
        return False
    try:
        from services.db.engine import engine
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL not set or Postgres unreachable."
)

OWNER_GMAIL = "owner@gmail.com"
REFRESH_MARK = "REFRESH-"
ACCESS_MARK = "ACCESS-"


class FakeGmailOAuthClient:
    """Records calls; returns canned tokens/identity. Never hits Google."""

    def __init__(self, email=OWNER_GMAIL, sub="sub-owner-123", scopes=None):
        from services.oauth.config import GMAIL_SCOPES
        self.email = email
        self.sub = sub
        self.scopes = scopes if scopes is not None else list(GMAIL_SCOPES)
        self.revoked: list[str] = []
        self.auth_urls: list[str] = []

    def authorization_url(self, *, state, code_challenge, redirect_uri):
        from services.oauth.config import GMAIL_SCOPES
        url = (f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"
               f"&code_challenge={code_challenge}&code_challenge_method=S256"
               f"&redirect_uri={redirect_uri}&scope={'+'.join(GMAIL_SCOPES)}")
        self.auth_urls.append(url)
        return url

    def exchange_code(self, *, code, code_verifier, redirect_uri):
        from services.oauth.gmail_client import TokenExchangeResult
        return TokenExchangeResult(
            refresh_token=REFRESH_MARK + code, access_token=ACCESS_MARK + code,
            account_email=self.email, account_sub=self.sub, scopes=self.scopes,
        )

    def refresh_access_token(self, refresh_token):
        return ACCESS_MARK + "refreshed-" + refresh_token

    def revoke(self, refresh_token):
        self.revoked.append(refresh_token)


def _mk_tenant_user(session, email, subject_suffix):
    from services.db import models as orm
    subject = f"s23-{subject_suffix}-{uuid.uuid4().hex[:8]}"
    t = orm.Tenant(name="T-" + subject); session.add(t); session.flush()
    u = orm.AppUser(tenant_id=t.id, idp_subject=subject, email=email); session.add(u); session.flush()
    session.add(orm.TenantMembership(user_id=u.id, role="creator")); session.commit()
    return t, u


def _principal(t, u, email):
    from services.api.auth import Principal
    return Principal(user_id=str(u.id), tenant_id=str(t.id), email=email,
                     roles=frozenset({"creator"}), is_dev=False)


@pytest.fixture()
def env(monkeypatch):
    from fastapi.testclient import TestClient

    from services.api.auth import get_principal
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.oauth.gmail_client import set_oauth_client
    from services.oauth.vault import DevTokenVault, set_vault

    # Flow tests run in production auth-mode with an injected owner principal, but
    # the dev token vault is explicitly permitted for tests.
    monkeypatch.setenv("EKC_ALLOW_DEV_VAULT", "1")

    session = SessionLocal()
    t_owner, u_owner = _mk_tenant_user(session, OWNER_GMAIL, "owner")
    t_other, u_other = _mk_tenant_user(session, "other@rival.com", "other")
    mbx = orm.Mailbox(provider="gmail", owner_email=OWNER_GMAIL, embed_model="deferred",
                      embed_dim=0, config={}, tenant_id=t_owner.id, owner_user_id=u_owner.id)
    session.add(mbx); session.commit()
    mid = str(mbx.id)

    fake = FakeGmailOAuthClient()
    set_oauth_client(fake)
    vault = DevTokenVault(refresher=fake.refresh_access_token, revoker=fake.revoke)
    set_vault(vault)

    client = TestClient(app)
    ns = SimpleNamespace(
        client=client, session=session, app=app, mid=mid, fake=fake, vault=vault,
        get_principal=get_principal,
        owner=_principal(t_owner, u_owner, OWNER_GMAIL),
        other=_principal(t_other, u_other, "other@rival.com"),
        t_owner=t_owner, u_owner=u_owner,
    )

    def as_owner():
        app.dependency_overrides[get_principal] = lambda: ns.owner
    def as_other():
        app.dependency_overrides[get_principal] = lambda: ns.other
    ns.as_owner = as_owner
    ns.as_other = as_other

    try:
        yield ns
    finally:
        app.dependency_overrides.clear()
        set_oauth_client(None)
        set_vault(None)
        session.execute(orm.OAuthState.__table__.delete().where(orm.OAuthState.mailbox_id == mid))
        session.execute(orm.MailboxProviderAccount.__table__.delete().where(
            orm.MailboxProviderAccount.mailbox_id == mid))
        session.execute(orm.AuditLog.__table__.delete().where(orm.AuditLog.mailbox_id == mid))
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        for u in (u_owner, u_other):
            session.execute(orm.TenantMembership.__table__.delete().where(
                orm.TenantMembership.user_id == u.id))
            session.execute(orm.AppUser.__table__.delete().where(orm.AppUser.id == u.id))
        for t in (t_owner, t_other):
            session.execute(orm.Tenant.__table__.delete().where(orm.Tenant.id == t.id))
        session.commit(); session.close()


def _start(env):
    return env.client.post(f"/api/mailbox/{env.mid}/gmail/connect/start")


def _fresh():
    from services.db.engine import SessionLocal
    return SessionLocal()


# ── Start: owner auth + state binding ────────────────────────────────────────

@requires_db
def test_start_requires_owner_auth(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    # No principal → 401.
    assert _start(env).status_code == 401
    # Different user/tenant → 404.
    env.as_other()
    assert _start(env).status_code == 404


@requires_db
def test_start_creates_bound_state_and_auth_url(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    r = _start(env)
    assert r.status_code == 200, r.text
    url = r.json()["authorization_url"]
    assert "code_challenge_method=S256" in url and "gmail.readonly" in url
    # No client secret leaks into the URL.
    assert "client_secret" not in url

    s = _fresh()
    try:
        from services.db import models as orm
        from sqlalchemy import select
        rows = s.execute(select(orm.OAuthState).where(orm.OAuthState.mailbox_id == env.mid)).scalars().all()
        assert len(rows) == 1
        st = rows[0]
        assert st.tenant_id == env.owner.tenant_id and st.user_id == env.owner.user_id
        assert st.provider == "gmail" and st.consumed_at is None and st.code_verifier
    finally:
        s.close()


# ── Callback: state validation + replay ──────────────────────────────────────

@requires_db
def test_callback_rejects_missing_invalid_and_replayed_state(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    # missing / unknown state
    r = env.client.get("/api/oauth/gmail/callback?state=nope&code=c", follow_redirects=False)
    assert r.status_code == 302 and "gmail_connect=invalid_state" in r.headers["location"]

    # valid state → success once
    _start(env)
    s = _fresh()
    try:
        from services.db import models as orm
        from sqlalchemy import select
        state = s.execute(select(orm.OAuthState.id).where(orm.OAuthState.mailbox_id == env.mid)).scalars().first()
    finally:
        s.close()
    r1 = env.client.get(f"/api/oauth/gmail/callback?state={state}&code=abc", follow_redirects=False)
    assert "gmail_connect=connected" in r1.headers["location"]
    # replay of the same (now consumed) state → rejected
    r2 = env.client.get(f"/api/oauth/gmail/callback?state={state}&code=abc", follow_redirects=False)
    assert "gmail_connect=invalid_state" in r2.headers["location"]


@requires_db
def test_callback_rejects_account_mismatch(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    env.fake.email = "attacker@evil.com"  # != mailbox.owner_email
    _start(env)
    s = _fresh()
    try:
        from services.db import models as orm
        from sqlalchemy import select
        state = s.execute(select(orm.OAuthState.id).where(orm.OAuthState.mailbox_id == env.mid)).scalars().first()
    finally:
        s.close()
    r = env.client.get(f"/api/oauth/gmail/callback?state={state}&code=abc", follow_redirects=False)
    assert "gmail_connect=account_mismatch" in r.headers["location"]
    # No provider account bound, no vault entry.
    s = _fresh()
    try:
        from services.db import models as orm
        from sqlalchemy import select
        assert s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalars().first() is None
    finally:
        s.close()


# ── Success: only vault_ref + safe metadata in the DB; no raw tokens ──────────

def _connect_ok(env):
    env.as_owner()
    _start(env)
    s = _fresh()
    try:
        from services.db import models as orm
        from sqlalchemy import select
        state = s.execute(select(orm.OAuthState.id).where(orm.OAuthState.mailbox_id == env.mid)).scalars().first()
    finally:
        s.close()
    return env.client.get(f"/api/oauth/gmail/callback?state={state}&code=abc", follow_redirects=False)


@requires_db
def test_success_stores_only_vault_ref_and_safe_metadata(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    r = _connect_ok(env)
    assert "gmail_connect=connected" in r.headers["location"]

    s = _fresh()
    try:
        from services.db import models as orm
        from sqlalchemy import select
        acct = s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalar_one()
        assert acct.status == "connected"
        assert acct.provider_account_email == OWNER_GMAIL
        assert acct.vault_ref and acct.vault_ref.startswith("gmail:")
        assert any("gmail.readonly" in sc for sc in acct.scopes_granted)
        # No raw token in ANY column of the row.
        blob = " ".join(str(getattr(acct, c.name)) for c in acct.__table__.columns)
        assert REFRESH_MARK not in blob and ACCESS_MARK not in blob
    finally:
        s.close()

    # Vault holds the ref; access token is minted (never stored).
    assert env.vault.exists(acct.vault_ref)
    assert env.vault.get_access_token(acct.vault_ref).startswith(ACCESS_MARK)


@requires_db
def test_no_raw_token_in_status_response_or_audit(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    _connect_ok(env)
    env.as_owner()
    # status response is safe
    st = env.client.get(f"/api/mailbox/{env.mid}/gmail/status")
    assert st.status_code == 200
    body = st.text
    assert st.json()["connected"] is True
    assert REFRESH_MARK not in body and ACCESS_MARK not in body and "vault_ref" not in body

    # audit metadata is safe
    s = _fresh()
    try:
        from services.db import models as orm
        from sqlalchemy import select
        logs = s.execute(select(orm.AuditLog).where(orm.AuditLog.mailbox_id == env.mid)).scalars().all()
        actions = {log.action for log in logs}
        assert {"oauth_start_created", "provider_account_connected", "oauth_callback_succeeded"} <= actions
        for log in logs:
            blob = f"{log.actor} {log.action} {log.scope}"
            assert REFRESH_MARK not in blob and ACCESS_MARK not in blob
    finally:
        s.close()


# ── Disconnect ───────────────────────────────────────────────────────────────

@requires_db
def test_disconnect_revokes_and_marks_inactive(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    _connect_ok(env)
    s = _fresh()
    try:
        from services.db import models as orm
        from sqlalchemy import select
        ref = s.execute(select(orm.MailboxProviderAccount.vault_ref).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalars().first()
    finally:
        s.close()

    env.as_owner()
    r = env.client.post(f"/api/mailbox/{env.mid}/gmail/disconnect")
    assert r.status_code == 200 and r.json()["disconnected"] is True
    assert env.fake.revoked  # provider-side revoke called
    assert not env.vault.exists(ref)  # vault entry gone

    s = _fresh()
    try:
        from services.db import models as orm
        from sqlalchemy import select
        acct = s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalar_one()
        assert acct.status == "disconnected" and acct.vault_ref is None
    finally:
        s.close()


# ── Resolver: production requires a connected account; dev uses env token ─────

@requires_db
def test_production_resolver_requires_connected_account(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    from services.oauth import flow
    from services.oauth.resolver import resolve_gmail_grant

    s = _fresh()
    try:
        with pytest.raises(flow.ProviderNotConnected):
            resolve_gmail_grant(s, env.mid, vault=env.vault)
    finally:
        s.close()

    _connect_ok(env)  # now a connected account exists
    s = _fresh()
    try:
        grant = resolve_gmail_grant(s, env.mid, vault=env.vault)
        assert grant["token"].startswith(ACCESS_MARK)  # minted via vault
    finally:
        s.close()


@requires_db
def test_dev_mode_uses_env_token_path(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv(f"GMAIL_TOKEN_{env.mid}", '{"token": "dev-env-token"}')
    from services.oauth.resolver import resolve_gmail_grant
    s = _fresh()
    try:
        grant = resolve_gmail_grant(s, env.mid, vault=env.vault)
        assert grant == {"token": "dev-env-token"}  # env path, not the vault
    finally:
        s.close()


# ── Access-log redaction (OAuth code/state must never reach logs) ────────────

def test_redact_target_strips_oauth_code_and_state():
    from services.api.log_redaction import redact_target
    out = redact_target("/api/oauth/gmail/callback?code=secret-code&state=secret-state")
    assert "secret-code" not in out and "secret-state" not in out
    assert "REDACTED" in out
    assert out.startswith("/api/oauth/gmail/callback?")  # path preserved
    # non-sensitive params pass through; no query string untouched
    assert redact_target("/api/projects/abc") == "/api/projects/abc"
    assert "mailbox_id=xyz" in redact_target("/api/preflight?mailbox_id=xyz&code=abc")


def test_uvicorn_access_log_record_is_redacted():
    """Simulate a uvicorn access record for the callback and confirm the emitted
    log line contains no raw code/state (the exact acceptance scenario)."""
    import io
    import logging

    from services.api.log_redaction import install_access_log_redaction

    install_access_log_redaction()
    logger = logging.getLogger("uvicorn.access")
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    prev_level, prev_prop = logger.level, logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:54321", "GET",
            "/api/oauth/gmail/callback?code=secret-code&state=secret-state", "1.1", 302,
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        logger.propagate = prev_prop
    out = buf.getvalue()
    assert "secret-code" not in out and "secret-state" not in out
    assert "REDACTED" in out
    assert "/api/oauth/gmail/callback" in out  # path still logged for observability


# ── Recipient routes untouched ───────────────────────────────────────────────

@requires_db
def test_recipient_route_unaffected_by_oauth(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    # Recipient session route is not owner-guarded and never uses OAuth/vault.
    r = env.client.post("/api/handoff/recipient/session", json={"code": "not-a-real-code"})
    assert r.status_code != 401
