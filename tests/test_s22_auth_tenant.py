"""S22 — auth + tenant boundary (DB-gated).

Implements docs/s19-auth-tenant-boundary-plan.md. Verifies:
  - dev mode preserves local/demo access (the dev principal owns mailboxes);
  - production/unset mode fails closed (401) without a principal;
  - a mailbox owner can act on their own mailbox/package;
  - a different user/tenant gets 404 (no cross-tenant existence oracle);
  - recipient routes are NOT owner-guarded (they keep package-session auth) and
    are never blocked by the creator-auth layer.

All Gmail/LLM-free; deterministic. Ownership tests run in production mode and
inject a Principal via app.dependency_overrides.
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


def _mk_tenant_user(session, email: str):
    from services.db import models as orm
    subject = "s22-" + uuid.uuid4().hex[:10]
    t = orm.Tenant(name="T-" + subject)
    session.add(t)
    session.flush()
    u = orm.AppUser(tenant_id=t.id, idp_subject=subject, email=email)
    session.add(u)
    session.flush()
    session.add(orm.TenantMembership(user_id=u.id, role="creator"))
    session.commit()
    return t, u


def _principal(t, u, email):
    from services.api.auth import Principal
    return Principal(
        user_id=str(u.id), tenant_id=str(t.id), email=email,
        roles=frozenset({"creator"}), is_dev=False,
    )


@pytest.fixture()
def env():
    from fastapi.testclient import TestClient

    from services.api.auth import get_principal
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from sqlalchemy import select

    session = SessionLocal()
    t_owner, u_owner = _mk_tenant_user(session, "owner@acme.corp")
    t_other, u_other = _mk_tenant_user(session, "other@rival.corp")
    mbx = orm.Mailbox(
        provider="gmail", owner_email="owner@acme.corp", embed_model="deferred",
        embed_dim=0, config={}, tenant_id=t_owner.id, owner_user_id=u_owner.id,
    )
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)

    client = TestClient(app)
    ns = SimpleNamespace(
        client=client, session=session, app=app, mid=mid,
        get_principal=get_principal,
        owner=_principal(t_owner, u_owner, "owner@acme.corp"),
        other=_principal(t_other, u_other, "other@rival.corp"),
    )
    try:
        yield ns
    finally:
        app.dependency_overrides.clear()
        # Clean up packages/audit for this mailbox, then the mailbox + auth rows.
        pkg_ids = select(orm.HandoffPackage.id).where(orm.HandoffPackage.mailbox_id == mid)
        session.execute(orm.HandoffAuditEvent.__table__.delete().where(
            orm.HandoffAuditEvent.package_id.in_(pkg_ids)
        ))
        session.execute(orm.HandoffScope.__table__.delete().where(
            orm.HandoffScope.package_id.in_(pkg_ids)
        ))
        session.execute(orm.HandoffPackage.__table__.delete().where(
            orm.HandoffPackage.mailbox_id == mid
        ))
        session.execute(orm.AuditLog.__table__.delete().where(orm.AuditLog.mailbox_id == mid))
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        for u in (u_owner, u_other):
            session.execute(orm.TenantMembership.__table__.delete().where(
                orm.TenantMembership.user_id == u.id
            ))
            session.execute(orm.AppUser.__table__.delete().where(orm.AppUser.id == u.id))
        for t in (t_owner, t_other):
            session.execute(orm.Tenant.__table__.delete().where(orm.Tenant.id == t.id))
        session.commit()
        session.close()


# ── Auth-mode helper is env-driven and dynamic ───────────────────────────────

def test_get_auth_mode_defaults_to_production(monkeypatch):
    from services.api.auth import get_auth_mode
    monkeypatch.delenv("AUTH_MODE", raising=False)
    assert get_auth_mode() == "production"
    monkeypatch.setenv("AUTH_MODE", "banana")
    assert get_auth_mode() == "production"
    monkeypatch.setenv("AUTH_MODE", "dev")
    assert get_auth_mode() == "dev"


# ── Dev mode preserves local/demo access ─────────────────────────────────────

@requires_db
def test_dev_mode_preserves_mailbox_access(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")  # the local/demo default
    # No principal override: the dev principal is synthesized and owns the mailbox.
    resp = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation", "title": "s22 dev"})
    assert resp.status_code == 200, resp.text
    pkg_id = resp.json()["id"]
    assert env.client.get(f"/api/handoff/{pkg_id}").status_code == 200


# ── Production fails closed without a principal ───────────────────────────────

@requires_db
def test_production_fails_closed_without_principal(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    # No dependency override → no principal source is wired → 401.
    resp = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation", "title": "x"})
    assert resp.status_code == 401
    assert env.client.get(f"/api/network-map/{env.mid}").status_code == 401


# ── Owner can access; a different user/tenant gets 404 ────────────────────────

@requires_db
def test_owner_access_and_cross_tenant_404(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")

    # As the owner: create + read a package on the owned mailbox.
    env.app.dependency_overrides[env.get_principal] = lambda: env.owner
    resp = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation", "title": "owned"})
    assert resp.status_code == 200, resp.text
    pkg_id = resp.json()["id"]
    assert env.client.get(f"/api/handoff/{pkg_id}").status_code == 200
    assert env.client.get(f"/api/network-map/{env.mid}").status_code in (200, 404)  # owned: not 404-by-authz

    # As a different user in another tenant: the SAME mailbox/package is 404.
    env.app.dependency_overrides[env.get_principal] = lambda: env.other
    assert env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation", "title": "nope"}).status_code == 404
    assert env.client.get(f"/api/handoff/{pkg_id}").status_code == 404
    assert env.client.get(f"/api/network-map/{env.mid}").status_code == 404
    assert env.client.get(f"/api/projects/{env.mid}").status_code == 404


# ── Recipient routes are NOT owner-guarded ───────────────────────────────────

@requires_db
def test_recipient_routes_not_owner_guarded(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    # No principal, production mode: the creator-auth layer must NOT block the
    # recipient session route. An unknown code yields the neutral "unavailable"
    # response (never a 401 from owner-auth).
    resp = env.client.post("/api/handoff/recipient/session", json={"code": "not-a-real-code"})
    assert resp.status_code != 401
