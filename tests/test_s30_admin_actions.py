"""S30 — audited admin actions (DB-gated): revoke package + disconnect provider.

Both actions are tenant-admin-only, reason-gated (422 on blank), tenant-scoped
(cross-tenant → 404), audited with safe metadata, and must not leak content or
tokens. Revoke reuses the S17 creator revoke lifecycle (blocks recipient access +
kills sessions); disconnect reuses the S23 vault-revoke path.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
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

EVIDENCE_BODY = "SENTINEL-s30-evidence-body"
CLAIM_TEXT = "SENTINEL-s30-claim-text"
VAULT_REF = "SENTINEL-s30-vault-ref"
SESSION_HASH = "SENTINEL-s30-session-hash"
_FORBIDDEN = [EVIDENCE_BODY, CLAIM_TEXT, VAULT_REF, SESSION_HASH]


class FakeVault:
    def __init__(self):
        self.revoked = []

    def revoke(self, vault_ref):
        self.revoked.append(vault_ref)


def _mk_tenant(session, name):
    from services.db import models as orm
    t = orm.Tenant(name="S30-" + name + "-" + uuid.uuid4().hex[:8])
    session.add(t)
    session.flush()
    return t


def _mk_user(session, tenant, email, role):
    from services.db import models as orm
    u = orm.AppUser(tenant_id=tenant.id, idp_subject="s30-" + uuid.uuid4().hex[:10], email=email)
    session.add(u)
    session.flush()
    session.add(orm.TenantMembership(user_id=u.id, role=role))
    session.flush()
    return u


def _principal(tenant, user, email, roles):
    from services.api.auth import Principal
    return Principal(user_id=str(user.id), tenant_id=str(tenant.id), email=email,
                     roles=frozenset(roles), is_dev=False)


@pytest.fixture()
def env():
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from services.api.auth import get_principal
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.oauth import vault as vaultmod

    s = SessionLocal()
    t = _mk_tenant(s, "main")
    t2 = _mk_tenant(s, "other")
    u_admin = _mk_user(s, t, "admin@acme.corp", "admin")
    u_reviewer = _mk_user(s, t, "reviewer@acme.corp", "security_reviewer")
    u_creator = _mk_user(s, t, "creator@acme.corp", "creator")
    u_other_admin = _mk_user(s, t2, "admin@rival.corp", "admin")

    mbx = orm.Mailbox(provider="gmail", owner_email="creator@acme.corp", embed_model="deferred",
                      embed_dim=0, config={}, tenant_id=t.id, owner_user_id=u_creator.id)
    s.add(mbx)
    s.flush()
    mid = str(mbx.id)

    lineage = str(uuid.uuid4())
    pkg = orm.HandoffPackage(
        mailbox_id=mid, creator_email="creator@acme.corp", status="published",
        reason="vacation", title="coverage", policy_mode="standard", version=1,
        lineage_id=lineage, published_at=datetime.now(timezone.utc),
    )
    s.add(pkg)
    s.flush()
    pid = str(pkg.id)
    rec = orm.HandoffRecipient(
        package_id=pid, recipient_email="rec@partner.example",
        capability_code_hash="code-hash-" + uuid.uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    s.add(rec)
    s.flush()
    s.add(orm.HandoffRecipientSession(
        package_id=pid, recipient_id=rec.id, session_token_hash=SESSION_HASH,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    s.add(orm.HandoffEvidence(
        package_id=pid, message_id_header="mid", subject="subj",
        body_snapshot=EVIDENCE_BODY,
    ))
    s.add(orm.HandoffClaim(
        package_id=pid, kind="briefing", text=CLAIM_TEXT,
        source_message_id_headers=["hdr"], confidence=0.9,
    ))
    acct = orm.MailboxProviderAccount(
        tenant_id=t.id, owner_user_id=u_creator.id, mailbox_id=mid, provider="gmail",
        provider_account_email="mbx@gmail.com", vault_ref=VAULT_REF,
        scopes_granted=["https://www.googleapis.com/auth/gmail.readonly"], status="connected",
    )
    s.add(acct)
    s.commit()
    aid = str(acct.id)

    fake_vault = FakeVault()
    original_vault = vaultmod._vault
    vaultmod.set_vault(fake_vault)

    client = TestClient(app)
    ns = SimpleNamespace(
        client=client, app=app, session=s, get_principal=get_principal,
        mid=mid, pid=pid, aid=aid, lineage=lineage, fake_vault=fake_vault, orm=orm,
        admin=_principal(t, u_admin, "admin@acme.corp", {"admin"}),
        reviewer=_principal(t, u_reviewer, "reviewer@acme.corp", {"security_reviewer"}),
        creator=_principal(t, u_creator, "creator@acme.corp", {"creator"}),
        other_admin=_principal(t2, u_other_admin, "admin@rival.corp", {"admin"}),
    )
    try:
        yield ns
    finally:
        app.dependency_overrides.clear()
        vaultmod.set_vault(original_vault)
        pkg_ids = select(orm.HandoffPackage.id).where(orm.HandoffPackage.mailbox_id == mid)
        s.execute(orm.HandoffAuditEvent.__table__.delete().where(orm.HandoffAuditEvent.package_id.in_(pkg_ids)))
        s.execute(orm.HandoffPackage.__table__.delete().where(orm.HandoffPackage.mailbox_id == mid))
        s.execute(orm.MailboxProviderAccount.__table__.delete().where(orm.MailboxProviderAccount.tenant_id.in_([t.id, t2.id])))
        s.execute(orm.AuditLog.__table__.delete().where(orm.AuditLog.mailbox_id == mid))
        s.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        for u in (u_admin, u_reviewer, u_creator, u_other_admin):
            s.execute(orm.TenantMembership.__table__.delete().where(orm.TenantMembership.user_id == u.id))
            s.execute(orm.AppUser.__table__.delete().where(orm.AppUser.id == u.id))
        for tt in (t, t2):
            s.execute(orm.Tenant.__table__.delete().where(orm.Tenant.id == tt.id))
        s.commit()
        s.close()


def _as(env, principal):
    env.app.dependency_overrides[env.get_principal] = lambda: principal


# ── Revoke package ────────────────────────────────────────────────────────────

@requires_db
def test_admin_revoke_blocks_recipient_and_audits(env):
    _as(env, env.admin)
    r = env.client.post(f"/api/admin/packages/{env.pid}/revoke", json={"reason": "policy breach"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "revoked"

    env.session.expire_all()
    orm = env.orm
    from sqlalchemy import select
    pkg = env.session.get(orm.HandoffPackage, env.pid)
    assert pkg.status == "revoked" and pkg.revoked_at is not None
    rec = env.session.execute(select(orm.HandoffRecipient).where(orm.HandoffRecipient.package_id == env.pid)).scalar_one()
    assert rec.revoked_at is not None                      # recipient grant revoked
    sess = env.session.execute(select(orm.HandoffRecipientSession).where(orm.HandoffRecipientSession.package_id == env.pid)).scalar_one()
    assert sess.revoked_at is not None                     # live session killed

    # Safe admin audit event with the reason; no content sentinels.
    events = env.session.execute(
        select(orm.HandoffAuditEvent).where(orm.HandoffAuditEvent.package_id == env.pid)
    ).scalars().all()
    ev = next(e for e in events if e.action == "package.revoked_by_admin")
    assert ev.metadata_.get("reason") == "policy breach"
    assert ev.metadata_.get("prior_status") == "published"
    assert "admin_user_id" in ev.metadata_
    for bad in _FORBIDDEN:
        assert bad not in str(ev.metadata_)


@requires_db
def test_revoke_reason_required(env):
    _as(env, env.admin)
    assert env.client.post(f"/api/admin/packages/{env.pid}/revoke", json={}).status_code == 422
    assert env.client.post(f"/api/admin/packages/{env.pid}/revoke", json={"reason": "   "}).status_code == 422


@requires_db
def test_revoke_denied_for_reviewer_and_creator(env):
    for p in (env.reviewer, env.creator):
        _as(env, p)
        assert env.client.post(f"/api/admin/packages/{env.pid}/revoke", json={"reason": "x"}).status_code == 403


@requires_db
def test_revoke_cross_tenant_404(env):
    _as(env, env.other_admin)
    assert env.client.post(f"/api/admin/packages/{env.pid}/revoke", json={"reason": "x"}).status_code == 404


@requires_db
def test_revoke_malformed_id_404(env):
    _as(env, env.admin)
    assert env.client.post("/api/admin/packages/not-a-uuid/revoke", json={"reason": "x"}).status_code == 404


@requires_db
def test_revoke_idempotent_and_no_content_leak(env):
    _as(env, env.admin)
    r1 = env.client.post(f"/api/admin/packages/{env.pid}/revoke", json={"reason": "first"})
    assert r1.status_code == 200
    r2 = env.client.post(f"/api/admin/packages/{env.pid}/revoke", json={"reason": "second"})
    assert r2.status_code == 200 and r2.json()["status"] == "revoked"  # idempotent no-op
    for bad in _FORBIDDEN:
        assert bad not in r1.text and bad not in r2.text


# ── Disconnect provider account ───────────────────────────────────────────────

@requires_db
def test_admin_disconnect_revokes_vault_and_audits(env):
    _as(env, env.admin)
    r = env.client.post(f"/api/admin/provider-accounts/{env.aid}/disconnect", json={"reason": "offboarding"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "disconnected"
    assert "vault_ref" not in r.text and VAULT_REF not in r.text  # never exposed

    assert env.fake_vault.revoked == [VAULT_REF]                  # vault revoke happened

    env.session.expire_all()
    orm = env.orm
    from sqlalchemy import select
    acct = env.session.get(orm.MailboxProviderAccount, env.aid)
    assert acct.status == "disconnected" and acct.disconnected_at is not None
    assert acct.vault_ref is None                                 # ref purged

    audit = env.session.execute(
        select(orm.AuditLog).where(
            orm.AuditLog.mailbox_id == env.mid,
            orm.AuditLog.action == "provider_account_disconnected_by_admin",
        )
    ).scalar_one()
    assert audit.scope == "offboarding"                          # reason recorded (safe)
    assert audit.sync_token is None                              # no token metadata


@requires_db
def test_admin_disconnect_fails_closed_when_vault_unavailable(env, monkeypatch):
    """If the vault is unavailable, disconnect returns 503 and leaves the account
    (status + vault_ref) unchanged with no success audit — never orphan a token."""
    from services.oauth import vault as vaultmod

    def _boom():
        raise vaultmod.VaultError("vault unavailable")

    monkeypatch.setattr(vaultmod, "get_vault", _boom)
    _as(env, env.admin)
    r = env.client.post(f"/api/admin/provider-accounts/{env.aid}/disconnect", json={"reason": "x"})
    assert r.status_code == 503

    env.session.expire_all()
    orm = env.orm
    from sqlalchemy import select
    acct = env.session.get(orm.MailboxProviderAccount, env.aid)
    assert acct.status == "connected" and acct.vault_ref == VAULT_REF   # unchanged
    assert env.fake_vault.revoked == []                                 # no revoke attempted
    audits = env.session.execute(
        select(orm.AuditLog).where(
            orm.AuditLog.mailbox_id == env.mid,
            orm.AuditLog.action == "provider_account_disconnected_by_admin",
        )
    ).scalars().all()
    assert audits == []                                                 # no success audit


@requires_db
def test_admin_disconnect_fails_closed_when_revoke_raises(env, monkeypatch):
    """If vault.revoke raises, disconnect returns 503, rolls back, and leaves the
    account unchanged with no success audit."""
    def _boom(vault_ref):
        raise RuntimeError("provider revoke failed")

    monkeypatch.setattr(env.fake_vault, "revoke", _boom)
    _as(env, env.admin)
    r = env.client.post(f"/api/admin/provider-accounts/{env.aid}/disconnect", json={"reason": "x"})
    assert r.status_code == 503

    env.session.expire_all()
    orm = env.orm
    from sqlalchemy import select
    acct = env.session.get(orm.MailboxProviderAccount, env.aid)
    assert acct.status == "connected" and acct.vault_ref == VAULT_REF   # unchanged
    audits = env.session.execute(
        select(orm.AuditLog).where(
            orm.AuditLog.mailbox_id == env.mid,
            orm.AuditLog.action == "provider_account_disconnected_by_admin",
        )
    ).scalars().all()
    assert audits == []


@requires_db
def test_owner_disconnect_also_fails_closed_when_revoke_raises(env, monkeypatch):
    """The creator/owner Gmail disconnect shares the fail-closed semantics after the
    disconnect_account refactor: a revoke failure → 503, account unchanged."""
    def _boom(vault_ref):
        raise RuntimeError("provider revoke failed")

    monkeypatch.setattr(env.fake_vault, "revoke", _boom)
    _as(env, env.creator)  # the mailbox owner
    r = env.client.post(f"/api/mailbox/{env.mid}/gmail/disconnect")
    assert r.status_code == 503

    env.session.expire_all()
    orm = env.orm
    acct = env.session.get(orm.MailboxProviderAccount, env.aid)
    assert acct.status == "connected" and acct.vault_ref == VAULT_REF   # unchanged


@requires_db
def test_disconnect_reason_required(env):
    _as(env, env.admin)
    assert env.client.post(f"/api/admin/provider-accounts/{env.aid}/disconnect", json={}).status_code == 422
    assert env.client.post(f"/api/admin/provider-accounts/{env.aid}/disconnect", json={"reason": ""}).status_code == 422


@requires_db
def test_disconnect_denied_for_reviewer_and_creator(env):
    for p in (env.reviewer, env.creator):
        _as(env, p)
        assert env.client.post(f"/api/admin/provider-accounts/{env.aid}/disconnect", json={"reason": "x"}).status_code == 403


@requires_db
def test_disconnect_cross_tenant_404(env):
    _as(env, env.other_admin)
    assert env.client.post(f"/api/admin/provider-accounts/{env.aid}/disconnect", json={"reason": "x"}).status_code == 404


@requires_db
def test_disconnect_malformed_id_404(env):
    _as(env, env.admin)
    assert env.client.post("/api/admin/provider-accounts/not-a-uuid/disconnect", json={"reason": "x"}).status_code == 404


@requires_db
def test_no_admin_mutation_exposes_forbidden_fields(env):
    _as(env, env.admin)
    rv = env.client.post(f"/api/admin/packages/{env.pid}/revoke", json={"reason": "r"}).text
    rd = env.client.post(f"/api/admin/provider-accounts/{env.aid}/disconnect", json={"reason": "d"}).text
    for bad in _FORBIDDEN:
        assert bad not in rv and bad not in rd


# ── Recipient snapshot-only invariant untouched ───────────────────────────────

@requires_db
def test_recipient_invariant_untouched(env):
    from services.hosted_readiness import check_recipient_snapshot_only
    assert check_recipient_snapshot_only().status == "pass"
    # recipient session exchange still reachable (not blocked by admin/auth layer)
    assert env.client.post("/api/handoff/recipient/session", json={"code": "nope"}).status_code != 401
