"""S24 — background job infrastructure (DB-gated).

Implements docs/s21-background-job-orchestration-plan.md. Verifies enqueue →
claim → run → terminal for succeed/fail/partial/cancel, idempotency dedupe,
cross-tenant isolation, worker claim/lease/reclaim, safe-metadata sanitization,
and that recipient routes can never reach jobs. Uses the harmless `noop` job.
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


# A test-only handler that raises a RAW exception carrying sensitive text, to
# prove the worker records only the exception TYPE name (never str(e)).
def _register_boom():
    from services.jobs import registry

    if not registry.is_registered("s24_boom"):
        @registry.register("s24_boom")
        def _boom(ctx):  # noqa: ANN001
            raise ValueError("SENSITIVE-CONTENT-should-not-be-logged")


_register_boom()


def _mk_tenant_user(session, email):
    from services.db import models as orm
    subject = "s24-" + uuid.uuid4().hex[:10]
    t = orm.Tenant(name="T-" + subject); session.add(t); session.flush()
    u = orm.AppUser(tenant_id=t.id, idp_subject=subject, email=email); session.add(u); session.flush()
    session.add(orm.TenantMembership(user_id=u.id, role="creator")); session.commit()
    return t, u


def _principal(t, u, email):
    from services.api.auth import Principal
    return Principal(user_id=str(u.id), tenant_id=str(t.id), email=email,
                     roles=frozenset({"creator"}), is_dev=False)


def _fresh():
    from services.db.engine import SessionLocal
    return SessionLocal()


@pytest.fixture()
def env():
    from fastapi.testclient import TestClient

    from services.api.auth import get_principal
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal

    session = SessionLocal()
    t_owner, u_owner = _mk_tenant_user(session, "owner@acme.corp")
    t_other, u_other = _mk_tenant_user(session, "other@rival.corp")
    mbx = orm.Mailbox(provider="gmail", owner_email="owner@acme.corp", embed_model="deferred",
                      embed_dim=0, config={}, tenant_id=t_owner.id, owner_user_id=u_owner.id)
    session.add(mbx); session.commit()
    mid = str(mbx.id)

    client = TestClient(app)
    ns = SimpleNamespace(
        client=client, session=session, app=app, mid=mid,
        tenant_id=str(t_owner.id), user_id=str(u_owner.id),
        get_principal=get_principal,
        owner=_principal(t_owner, u_owner, "owner@acme.corp"),
        other=_principal(t_other, u_other, "other@rival.corp"),
    )
    ns.as_owner = lambda: app.dependency_overrides.__setitem__(get_principal, lambda: ns.owner)
    ns.as_other = lambda: app.dependency_overrides.__setitem__(get_principal, lambda: ns.other)
    try:
        yield ns
    finally:
        app.dependency_overrides.clear()
        session.execute(orm.Job.__table__.delete().where(orm.Job.mailbox_id == mid))
        session.execute(orm.AuditLog.__table__.delete().where(orm.AuditLog.mailbox_id == mid))
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        for u in (u_owner, u_other):
            session.execute(orm.TenantMembership.__table__.delete().where(orm.TenantMembership.user_id == u.id))
            session.execute(orm.AppUser.__table__.delete().where(orm.AppUser.id == u.id))
        for t in (t_owner, t_other):
            session.execute(orm.Tenant.__table__.delete().where(orm.Tenant.id == t.id))
        session.commit(); session.close()


def _enqueue(env, params=None, **kw):
    from services.jobs import service
    return service.enqueue(env.session, tenant_id=env.tenant_id, job_type="noop",
                           mailbox_id=env.mid, requested_by=env.user_id, params=params, **kw)


# ── enqueue → claim → run → terminal ─────────────────────────────────────────

@requires_db
def test_noop_success_lifecycle(env):
    from services.jobs import worker
    job = _enqueue(env)
    assert job.status == "queued"
    db = _fresh()
    try:
        ran = worker.run_once(db, worker_id="w1")
        assert ran is not None and ran.id == job.id
        assert ran.status == "succeeded"
        assert ran.summary == "noop complete"
        assert ran.started_at is not None and ran.finished_at is not None
        assert ran.progress.get("phase") == "done"
    finally:
        db.close()


@requires_db
def test_noop_failure_records_safe_category(env):
    from services.jobs import worker
    _enqueue(env, params={"fail": True})
    db = _fresh()
    try:
        ran = worker.run_once(db, worker_id="w1")
        assert ran.status == "failed"
        assert ran.error_category == "noop_failed"
        # no content in the error fields
        assert "SENSITIVE" not in (ran.error_message or "")
    finally:
        db.close()


@requires_db
def test_noop_partial_success(env):
    from services.jobs import worker
    _enqueue(env, params={"partial": True})
    db = _fresh()
    try:
        ran = worker.run_once(db, worker_id="w1")
        assert ran.status == "partially_succeeded"
        assert ran.progress.get("failed") == 1 and ran.progress.get("total") == 2
    finally:
        db.close()


@requires_db
def test_raw_exception_records_only_type_name(env):
    from services.jobs import service, worker
    service.enqueue(env.session, tenant_id=env.tenant_id, job_type="s24_boom",
                    mailbox_id=env.mid, requested_by=env.user_id)
    db = _fresh()
    try:
        ran = worker.run_once(db, worker_id="w1")
        assert ran.status == "failed"
        assert ran.error_category == "handler_error"
        assert ran.error_message == "ValueError"  # type name only
        assert "SENSITIVE" not in (ran.error_message or "")
    finally:
        db.close()


# ── cancel ───────────────────────────────────────────────────────────────────

@requires_db
def test_cancel_queued_job_immediately(env):
    from services.jobs import service
    job = _enqueue(env)
    service.request_cancel(env.session, job)
    env.session.refresh(job)
    assert job.status == "canceled" and job.finished_at is not None


@requires_db
def test_cancel_running_job_is_cooperative(env):
    from services.jobs import service, worker
    _enqueue(env)
    db = _fresh()
    try:
        claimed = worker.claim_next(db, worker_id="w1")
        assert claimed.status == "running"
        service.request_cancel(db, claimed)  # running → cancel_requested set
        assert claimed.status == "running"
        worker.run_claimed(db, claimed)  # noop checkpoint honors it
        db.refresh(claimed)
        assert claimed.status == "canceled"
    finally:
        db.close()


# ── idempotency ──────────────────────────────────────────────────────────────

@requires_db
def test_idempotency_dedupes_active_jobs(env):
    a = _enqueue(env, idempotency_key="dedupe-1")
    b = _enqueue(env, idempotency_key="dedupe-1")
    assert a.id == b.id  # same active job returned, not a duplicate


# ── worker claim / lease / reclaim ───────────────────────────────────────────

@requires_db
def test_claim_sets_lease_and_second_claim_is_empty(env):
    from services.jobs import worker
    _enqueue(env)
    db = _fresh()
    try:
        j = worker.claim_next(db, worker_id="w1", lease_seconds=60)
        assert j is not None and j.status == "running"
        assert j.worker_id == "w1" and j.lease_expires_at is not None and j.attempt == 1
        assert worker.claim_next(db, worker_id="w2") is None  # nothing else queued
    finally:
        db.close()


@requires_db
def test_expired_lease_is_reclaimed(env):
    from services.jobs import worker
    j = _enqueue(env)
    db = _fresh()
    try:
        c1 = worker.claim_next(db, worker_id="w1", lease_seconds=60)
        # Simulate a crashed worker: force the lease into the past.
        c1.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        c2 = worker.claim_next(db, worker_id="w2")
        assert c2 is not None and c2.id == j.id and c2.worker_id == "w2" and c2.attempt == 2
    finally:
        db.close()


# ── safe-metadata sanitization ───────────────────────────────────────────────

@requires_db
def test_enqueue_sanitizes_unsafe_params(env):
    job = _enqueue(env, params={
        "body": "secret email body", "token": "oauth-token", "prompt": "llm prompt",
        "recipient_email": "r@x.com", "fail": True, "phase": "queued", "steps": 3,
    })
    # Unsafe keys dropped; safe scalars kept.
    assert job.params == {"fail": True, "phase": "queued", "steps": 3}


# ── cross-tenant isolation + recipient cannot see jobs (API) ─────────────────

@requires_db
def test_api_owner_can_enqueue_and_cross_tenant_gets_404(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    r = env.client.post(f"/api/mailbox/{env.mid}/jobs", json={"job_type": "noop"})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    assert env.client.get(f"/api/jobs/{job_id}").status_code == 200
    # non-enqueuable type rejected
    assert env.client.post(f"/api/mailbox/{env.mid}/jobs", json={"job_type": "s24_boom"}).status_code == 422

    # different tenant/user: 404 for the job and the mailbox listing
    env.as_other()
    assert env.client.get(f"/api/jobs/{job_id}").status_code == 404
    assert env.client.post(f"/api/jobs/{job_id}/cancel").status_code == 404
    assert env.client.get(f"/api/mailbox/{env.mid}/jobs").status_code == 404


@requires_db
def test_recipient_cannot_reach_jobs(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    # No principal (as a recipient would have): job routes fail closed (401),
    # while the recipient session route is unaffected.
    a = env.client.get(f"/api/jobs/{uuid.uuid4()}")
    assert a.status_code == 401
    b = env.client.post(f"/api/mailbox/{env.mid}/jobs", json={"job_type": "noop"})
    assert b.status_code == 401
    rec = env.client.post("/api/handoff/recipient/session", json={"code": "nope"})
    assert rec.status_code != 401
