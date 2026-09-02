"""S50 - Google Calendar OAuth + token vault connect/status/disconnect (DB-gated).

Implements docs/s49-calendar-first-handoff-context-plan.md section 8. Verifies the
calendar connect flow reuses the S23 OAuth/vault boundary as a DISTINCT provider
(google_calendar): state+PKCE/mismatch/replay guards, the exact least-privilege
scope (calendar.events.readonly, never the broad calendar.readonly), the vault
boundary (DB stores only vault_ref + safe metadata; no raw tokens anywhere),
fail-closed disconnect, safe status, and that Gmail + recipient routes are
unaffected. No real Google calls - a fake OAuth client is injected. S50 does NOT
fetch calendar events.
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

OWNER = "owner@company.com"
REFRESH_MARK = "REFRESH-"
ACCESS_MARK = "ACCESS-"
CAL_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
BROAD_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class FakeCalendarOAuthClient:
    """Records calls; returns canned tokens/identity with calendar scopes. Never
    hits Google. Embeds CALENDAR_SCOPES in the auth URL like the real client."""

    def __init__(self, email=OWNER, sub="cal-sub-123", scopes=None):
        from services.oauth.config import CALENDAR_SCOPES
        self.email = email
        self.sub = sub
        self.scopes = scopes if scopes is not None else list(CALENDAR_SCOPES)
        self.revoked: list[str] = []
        self.auth_urls: list[str] = []

    def authorization_url(self, *, state, code_challenge, redirect_uri):
        from services.oauth.config import CALENDAR_SCOPES
        url = (f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"
               f"&code_challenge={code_challenge}&code_challenge_method=S256"
               f"&redirect_uri={redirect_uri}&scope={'+'.join(CALENDAR_SCOPES)}")
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
    subject = f"s50-{subject_suffix}-{uuid.uuid4().hex[:8]}"
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
    from services.oauth.gmail_client import set_calendar_oauth_client
    from services.oauth.vault import DevTokenVault, set_vault

    monkeypatch.setenv("EKC_ALLOW_DEV_VAULT", "1")

    session = SessionLocal()
    t_owner, u_owner = _mk_tenant_user(session, OWNER, "owner")
    t_other, u_other = _mk_tenant_user(session, "other@rival.com", "other")
    mbx = orm.Mailbox(provider="gmail", owner_email=OWNER, embed_model="deferred",
                      embed_dim=0, config={}, tenant_id=t_owner.id, owner_user_id=u_owner.id)
    session.add(mbx); session.commit()
    mid = str(mbx.id)

    fake = FakeCalendarOAuthClient()
    set_calendar_oauth_client(fake)
    vault = DevTokenVault(refresher=fake.refresh_access_token, revoker=fake.revoke)
    set_vault(vault)

    client = TestClient(app)
    ns = SimpleNamespace(
        client=client, session=session, app=app, mid=mid, fake=fake, vault=vault,
        get_principal=get_principal,
        owner=_principal(t_owner, u_owner, OWNER),
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
        set_calendar_oauth_client(None)
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
    return env.client.post(f"/api/mailbox/{env.mid}/calendar/connect/start")


def _fresh():
    from services.db.engine import SessionLocal
    return SessionLocal()


def _state(env):
    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        return s.execute(select(orm.OAuthState.id).where(
            orm.OAuthState.mailbox_id == env.mid)).scalars().first()
    finally:
        s.close()


def _connect_ok(env):
    env.as_owner()
    _start(env)
    return env.client.get(f"/api/oauth/calendar/callback?state={_state(env)}&code=abc",
                          follow_redirects=False)


# -- Start: owner auth + provider-bound state + exact scope --------------------

@requires_db
def test_start_requires_owner_auth(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    assert _start(env).status_code == 401          # no principal
    env.as_other()
    assert _start(env).status_code == 404          # cross-tenant


@requires_db
def test_start_binds_state_to_calendar_provider_with_exact_scope(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    r = _start(env)
    assert r.status_code == 200, r.text
    url = r.json()["authorization_url"]
    # Exactly calendar.events.readonly; no broad calendar.readonly, no Gmail scope.
    assert CAL_SCOPE.replace(":", "%3A").replace("/", "%2F") in url or CAL_SCOPE in url
    assert "calendar.events.readonly" in url
    assert "gmail.readonly" not in url
    # the broader calendar.readonly must NOT be present as its own scope token
    assert "auth/calendar.readonly" not in url
    assert "code_challenge_method=S256" in url and "client_secret" not in url

    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        st = s.execute(select(orm.OAuthState).where(
            orm.OAuthState.mailbox_id == env.mid)).scalar_one()
        assert st.provider == "google_calendar"
        assert st.consumed_at is None and st.code_verifier
    finally:
        s.close()


# -- Callback: state validation, replay, mismatch -----------------------------

@requires_db
def test_callback_rejects_missing_and_replayed_state(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    r = env.client.get("/api/oauth/calendar/callback?state=nope&code=c", follow_redirects=False)
    assert r.status_code == 302 and "calendar_connect=invalid_state" in r.headers["location"]

    _start(env)
    state = _state(env)
    r1 = env.client.get(f"/api/oauth/calendar/callback?state={state}&code=abc", follow_redirects=False)
    assert "calendar_connect=connected" in r1.headers["location"]
    r2 = env.client.get(f"/api/oauth/calendar/callback?state={state}&code=abc", follow_redirects=False)
    assert "calendar_connect=invalid_state" in r2.headers["location"]


@requires_db
def test_callback_rejects_account_mismatch_no_binding_no_token(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    env.fake.email = "attacker@evil.com"  # != mailbox.owner_email
    _start(env)
    r = env.client.get(f"/api/oauth/calendar/callback?state={_state(env)}&code=abc", follow_redirects=False)
    assert "calendar_connect=account_mismatch" in r.headers["location"]
    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        assert s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalars().first() is None
    finally:
        s.close()
    # No vault entry was created either.
    assert env.fake.revoked == []


# -- Success: only vault_ref + safe metadata, provider google_calendar --------

@requires_db
def test_success_stores_only_vault_ref_and_safe_metadata(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    r = _connect_ok(env)
    assert "calendar_connect=connected" in r.headers["location"]

    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        acct = s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalar_one()
        assert acct.provider == "google_calendar"
        assert acct.status == "connected"
        assert acct.provider_account_email == OWNER
        assert acct.vault_ref and acct.vault_ref.startswith("google_calendar:")
        assert CAL_SCOPE in acct.scopes_granted
        assert BROAD_SCOPE not in acct.scopes_granted
        # No raw token in ANY column.
        blob = " ".join(str(getattr(acct, c.name)) for c in acct.__table__.columns)
        assert REFRESH_MARK not in blob and ACCESS_MARK not in blob
    finally:
        s.close()

    assert env.vault.exists(acct.vault_ref)
    assert env.vault.get_access_token(acct.vault_ref).startswith(ACCESS_MARK)


@requires_db
def test_no_raw_secrets_in_status_or_audit(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    _connect_ok(env)
    env.as_owner()
    st = env.client.get(f"/api/mailbox/{env.mid}/calendar/status")
    assert st.status_code == 200
    body = st.text
    assert st.json()["connected"] is True
    assert st.json()["provider"] == "google_calendar"
    for banned in (REFRESH_MARK, ACCESS_MARK, "vault_ref", "code_verifier", "client_secret"):
        assert banned not in body

    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        logs = s.execute(select(orm.AuditLog).where(orm.AuditLog.mailbox_id == env.mid)).scalars().all()
        actions = {log.action for log in logs}
        assert {"oauth_start_created", "provider_account_connected", "oauth_callback_succeeded"} <= actions
        # Calendar audits carry the safe provider category, no tokens.
        cal_scopes = {log.scope for log in logs}
        assert "google_calendar" in cal_scopes
        for log in logs:
            blob = f"{log.actor} {log.action} {log.scope}"
            assert REFRESH_MARK not in blob and ACCESS_MARK not in blob
    finally:
        s.close()


@requires_db
def test_status_exposes_safe_metadata_only(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    # Not connected yet -> safe empty status.
    st0 = env.client.get(f"/api/mailbox/{env.mid}/calendar/status").json()
    assert st0["connected"] is False and st0["provider"] == "google_calendar"

    _connect_ok(env)
    env.as_owner()
    st = env.client.get(f"/api/mailbox/{env.mid}/calendar/status").json()
    assert set(st.keys()) == {
        "provider", "connected", "provider_account_email", "scopes_granted",
        "status", "connected_at", "last_verified_at", "disconnected_at",
    }
    assert st["connected"] is True and st["provider_account_email"] == OWNER
    assert st["connected_at"] and st["last_verified_at"]


# -- Disconnect (S30 fail-closed semantics) -----------------------------------

@requires_db
def test_disconnect_revokes_and_marks_disconnected(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    _connect_ok(env)
    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        ref = s.execute(select(orm.MailboxProviderAccount.vault_ref).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalars().first()
    finally:
        s.close()

    env.as_owner()
    r = env.client.post(f"/api/mailbox/{env.mid}/calendar/disconnect")
    assert r.status_code == 200 and r.json()["disconnected"] is True
    assert env.fake.revoked and not env.vault.exists(ref)

    s = _fresh()
    try:
        acct = s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalar_one()
        assert acct.status == "disconnected" and acct.vault_ref is None
    finally:
        s.close()


@requires_db
def test_disconnect_already_disconnected_is_idempotent(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    # No account at all -> idempotent 200 with disconnected False.
    r = env.client.post(f"/api/mailbox/{env.mid}/calendar/disconnect")
    assert r.status_code == 200 and r.json()["disconnected"] is False


@requires_db
def test_disconnect_fail_closed_on_vault_revoke_failure(env, monkeypatch):
    """A vault revoke failure -> 503 and NO DB mutation (account still connected,
    vault_ref intact)."""
    monkeypatch.setenv("AUTH_MODE", "production")
    _connect_ok(env)

    class BoomVault:
        def revoke(self, ref):
            raise RuntimeError("provider revoke failed")
    from services.oauth.vault import set_vault
    set_vault(BoomVault())

    env.as_owner()
    r = env.client.post(f"/api/mailbox/{env.mid}/calendar/disconnect")
    assert r.status_code == 503

    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        acct = s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalar_one()
        assert acct.status == "connected" and acct.vault_ref  # unchanged
    finally:
        s.close()


@requires_db
def test_disconnect_fail_closed_on_vault_unavailable(env, monkeypatch):
    """Vault unavailable at get_vault() -> 503 and no DB mutation."""
    monkeypatch.setenv("AUTH_MODE", "production")
    _connect_ok(env)

    import services.api.routers.oauth_calendar as cal
    from services.oauth.vault import VaultError
    monkeypatch.setattr(cal, "get_vault", lambda: (_ for _ in ()).throw(VaultError("down")))

    env.as_owner()
    r = env.client.post(f"/api/mailbox/{env.mid}/calendar/disconnect")
    assert r.status_code == 503

    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        acct = s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalar_one()
        assert acct.status == "connected" and acct.vault_ref
    finally:
        s.close()


# -- Isolation: Gmail + recipient routes unaffected ---------------------------

@requires_db
def test_calendar_connect_does_not_create_or_touch_gmail_account(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    _connect_ok(env)
    env.as_owner()
    # A calendar connect must not make the mailbox report a connected Gmail account.
    g = env.client.get(f"/api/mailbox/{env.mid}/gmail/status")
    assert g.status_code == 200 and g.json()["connected"] is False
    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        rows = s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalars().all()
        providers = sorted(r.provider for r in rows)
        assert providers == ["google_calendar"]  # exactly one, calendar only
    finally:
        s.close()


@requires_db
def test_recipient_route_unaffected_by_calendar_oauth(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    r = env.client.post("/api/handoff/recipient/session", json={"code": "not-a-real-code"})
    assert r.status_code != 401


# -- Callback access-log redaction already covers any path (param-based) ------

def test_calendar_callback_code_and_state_are_redacted_in_logs():
    from services.api.log_redaction import redact_target
    out = redact_target("/api/oauth/calendar/callback?code=secret-code&state=secret-state")
    assert "secret-code" not in out and "secret-state" not in out
    assert "REDACTED" in out
    assert out.startswith("/api/oauth/calendar/callback?")
