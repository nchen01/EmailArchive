"""S25 — Gmail date-range ingest moved onto the S24 job runner (DB-gated).

The confirm endpoint validates + verifies the account request-time, then enqueues
a `gmail_ingest_window` job; a worker runs the real ingest. Preview stays
synchronous (covered by test_s16). Here: idempotency dedupe of ingest jobs,
replace_snapshot forwarding, safe params, and recipient isolation.
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


def _fake_ingest_result(**kw):
    return {
        "messages": 2, "threads": 1, "people": 1, "edges": 0, "hit_cap": False,
        "replaced": kw.get("replace_snapshot", False), "cleared": None,
        "sync_token_disposition": "not_saved (date-windowed snapshot)", "persisted": True,
    }


@pytest.fixture()
def env(monkeypatch):
    from fastapi.testclient import TestClient

    from services.api.auth import get_principal
    from services.api.routers import gmail_ingest as router
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.ingest import gmail_windowed as gw
    from services.jobs.handlers import gmail_ingest as handler

    # Stub the provider/account/ingest so no real Gmail is touched.
    captured = {}

    def _fake(session, **kw):
        captured.update(kw)
        return _fake_ingest_result(**kw)

    monkeypatch.setattr(router, "verify_account", lambda *a, **k: None)
    monkeypatch.setattr(router, "_provider_for", lambda _id: object())
    monkeypatch.setattr(gw, "verify_account", lambda *a, **k: None)
    monkeypatch.setattr(gw, "run_windowed_ingest", _fake)
    monkeypatch.setattr(handler, "provider_factory", lambda _id: object())

    session = SessionLocal()
    t = orm.Tenant(name="T-" + uuid.uuid4().hex[:8]); session.add(t); session.flush()
    u = orm.AppUser(tenant_id=t.id, idp_subject="s25-" + uuid.uuid4().hex[:8], email="owner@acme.corp")
    session.add(u); session.flush()
    session.add(orm.TenantMembership(user_id=u.id, role="creator"))
    mbx = orm.Mailbox(provider="gmail", owner_email="owner@acme.corp", embed_model="deferred",
                      embed_dim=0, config={"internal_domains": ["acme.corp"]},
                      tenant_id=t.id, owner_user_id=u.id)
    session.add(mbx); session.commit()
    mid = str(mbx.id)

    from services.api.auth import Principal
    owner = Principal(user_id=str(u.id), tenant_id=str(t.id), email="owner@acme.corp",
                      roles=frozenset({"creator"}), is_dev=False)

    client = TestClient(app)
    ns = SimpleNamespace(client=client, session=session, mid=mid, captured=captured,
                         app=app, get_principal=get_principal, owner=owner, tenant_id=str(t.id))
    try:
        yield ns
    finally:
        app.dependency_overrides.clear()
        session.execute(orm.Job.__table__.delete().where(orm.Job.mailbox_id == mid))
        session.execute(orm.AuditLog.__table__.delete().where(orm.AuditLog.mailbox_id == mid))
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        session.execute(orm.TenantMembership.__table__.delete().where(orm.TenantMembership.user_id == u.id))
        session.execute(orm.AppUser.__table__.delete().where(orm.AppUser.id == u.id))
        session.execute(orm.Tenant.__table__.delete().where(orm.Tenant.id == t.id))
        session.commit(); session.close()


def _ingest(env, **body):
    body.setdefault("confirm", True)
    return env.client.post(f"/api/gmail-ingest/{env.mid}/ingest", json=body)


@requires_db
def test_confirm_enqueues_gmail_ingest_window_job_with_safe_params(env):
    r = _ingest(env, date_from="2026-04-01", date_to="2026-06-30", max_messages=100)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    from services.db import models as orm
    job = env.session.get(orm.Job, job_id)
    assert job.job_type == "gmail_ingest_window" and job.status == "queued"
    # params carry only safe scalars — no token/content keys.
    assert set(job.params) <= {"mailbox_id", "date_from", "date_to", "max_messages", "replace_snapshot", "internal_domains"}
    assert "token" not in str(job.params).lower()


@requires_db
def test_repeated_confirm_same_window_dedupes(env):
    a = _ingest(env, date_from="2026-04-01", date_to="2026-06-30")
    b = _ingest(env, date_from="2026-04-01", date_to="2026-06-30")
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["job_id"] == b.json()["job_id"]  # one active ingest job


@requires_db
def test_replace_snapshot_forwarded_to_runner(env):
    from services.db.engine import SessionLocal
    from services.jobs import worker as jobworker

    r = _ingest(env, date_from="2026-04-01", date_to="2026-06-30", replace_snapshot=True)
    assert r.status_code == 200 and r.json()["mode"] == "replace"
    db = SessionLocal()
    try:
        ran = jobworker.run_once(db, worker_id="w1")
        assert ran.status == "succeeded" and ran.progress.get("replaced") is True
    finally:
        db.close()
    assert env.captured.get("replace_snapshot") is True


@requires_db
def test_recipient_cannot_access_ingest_job(env, monkeypatch):
    # Enqueue as the owner (dev mode), then confirm a principal-less production
    # request (as a recipient would be) cannot read the job.
    r = _ingest(env, date_from="2026-04-01", date_to="2026-06-30")
    job_id = r.json()["job_id"]
    monkeypatch.setenv("AUTH_MODE", "production")
    assert env.client.get(f"/api/jobs/{job_id}").status_code == 401
    # recipient session route remains unaffected
    assert env.client.post("/api/handoff/recipient/session", json={"code": "nope"}).status_code != 401
