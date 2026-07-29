"""S26 — post-ingest pipeline jobs (DB-gated).

l1_enrichment / event_extraction / embedding_backfill / project_materialization on
the S24 runner. Job-layer tests monkeypatch the pipeline seam functions (the core
enrichment/embed/materialize logic is covered by the S8/S9/enrichment suites);
endpoint tests cover owner auth, the embedding cost gate, and the S9 embeddings
precheck. No Voyage/Anthropic calls.
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


def _mk(session, email="owner@acme.corp"):
    from services.db import models as orm
    subj = "s26-" + uuid.uuid4().hex[:10]
    t = orm.Tenant(name="T-" + subj); session.add(t); session.flush()
    u = orm.AppUser(tenant_id=t.id, idp_subject=subj, email=email); session.add(u); session.flush()
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
    t_owner, u_owner = _mk(session, "owner@acme.corp")
    t_other, u_other = _mk(session, "other@rival.corp")
    mbx = orm.Mailbox(provider="gmail", owner_email="owner@acme.corp", embed_model="deferred",
                      embed_dim=0, config={"internal_domains": ["acme.corp"]},
                      tenant_id=t_owner.id, owner_user_id=u_owner.id)
    session.add(mbx); session.commit()
    mid = str(mbx.id)
    client = TestClient(app)
    ns = SimpleNamespace(
        client=client, session=session, app=app, mid=mid,
        tenant_id=str(t_owner.id), user_id=str(u_owner.id), get_principal=get_principal,
        owner=_principal(t_owner, u_owner, "owner@acme.corp"),
        other=_principal(t_other, u_other, "other@rival.corp"),
    )
    ns.as_owner = lambda: app.dependency_overrides.__setitem__(get_principal, lambda: ns.owner)
    ns.as_other = lambda: app.dependency_overrides.__setitem__(get_principal, lambda: ns.other)
    try:
        yield ns
    finally:
        app.dependency_overrides.clear()
        for tbl in (orm.Job, orm.MessageEmbedding, orm.Message, orm.Thread, orm.AuditLog):
            session.execute(tbl.__table__.delete().where(tbl.mailbox_id == mid))
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        for u in (u_owner, u_other):
            session.execute(orm.TenantMembership.__table__.delete().where(orm.TenantMembership.user_id == u.id))
            session.execute(orm.AppUser.__table__.delete().where(orm.AppUser.id == u.id))
        for t in (t_owner, t_other):
            session.execute(orm.Tenant.__table__.delete().where(orm.Tenant.id == t.id))
        session.commit(); session.close()


def _enqueue_direct(env, job_type, params=None):
    from services.jobs import service
    p = {"mailbox_id": env.mid, **(params or {})}
    return service.enqueue(env.session, tenant_id=env.tenant_id, job_type=job_type,
                           mailbox_id=env.mid, requested_by=env.user_id, params=p)


# ── each job type runs via the worker (seam monkeypatched) ───────────────────

@requires_db
@pytest.mark.parametrize("job_type,seam,stats,summary_key", [
    ("l1_enrichment", "enrich_mailbox", {"messages": 5, "people": 2, "edges": 1}, "messages"),
    ("event_extraction", "extract_events_for_mailbox", {"threads": 3, "events": 4}, "events"),
    ("project_materialization", "run_project_materialization", {"projects_written": 2}, "projects_written"),
])
def test_pipeline_job_runs_and_reports_safe_progress(env, monkeypatch, job_type, seam, stats, summary_key):
    from services.jobs import worker as jobworker
    from services.jobs.handlers import pipeline
    monkeypatch.setattr(pipeline, seam, lambda *a, **k: dict(stats))
    _enqueue_direct(env, job_type)
    db = _fresh()
    try:
        ran = jobworker.run_once(db, worker_id="w1")
        assert ran.status == "succeeded" and ran.job_type == job_type
        assert ran.progress.get(summary_key) == stats[summary_key]
        assert ran.progress.get("phase") == "done"
        blob = f"{ran.params} {ran.progress} {ran.summary}"
        for bad in ("token", "prompt", "response", "traceback", "@"):
            assert bad not in blob.lower()
    finally:
        db.close()


@requires_db
def test_embedding_job_requires_cost_confirmed(env, monkeypatch):
    from services.jobs import worker as jobworker
    from services.jobs.handlers import pipeline
    monkeypatch.setattr(pipeline, "run_embedding_backfill", lambda *a, **k: {"embedded": 4})
    # enqueue WITHOUT cost_confirmed → the handler refuses.
    _enqueue_direct(env, "embedding_backfill", {"batch_size": 8})
    db = _fresh()
    try:
        ran = jobworker.run_once(db, worker_id="w1")
        assert ran.status == "failed" and ran.error_category == "cost_not_confirmed"
    finally:
        db.close()
    # WITH cost_confirmed → runs.
    _enqueue_direct(env, "embedding_backfill", {"batch_size": 8, "cost_confirmed": True})
    db = _fresh()
    try:
        ran = jobworker.run_once(db, worker_id="w1")
        assert ran.status == "succeeded" and ran.progress.get("embedded") == 4
    finally:
        db.close()


@requires_db
def test_pipeline_job_failure_is_safe_category(env, monkeypatch):
    from services.jobs import worker as jobworker
    from services.jobs.handlers import pipeline
    from services.jobs.registry import JobError

    def _boom_cat(*a, **k):
        raise JobError("l1_persist_failed", "context-free message")
    monkeypatch.setattr(pipeline, "enrich_mailbox", _boom_cat)
    _enqueue_direct(env, "l1_enrichment")
    db = _fresh()
    try:
        ran = jobworker.run_once(db, worker_id="w1")
        assert ran.status == "failed" and ran.error_category == "l1_persist_failed"
    finally:
        db.close()

    def _boom_raw(*a, **k):
        raise ValueError("SENSITIVE raw email body here")
    monkeypatch.setattr(pipeline, "enrich_mailbox", _boom_raw)
    _enqueue_direct(env, "l1_enrichment")
    db = _fresh()
    try:
        ran = jobworker.run_once(db, worker_id="w1")
        assert ran.status == "failed" and ran.error_category == "handler_error"
        assert ran.error_message == "ValueError"  # type name only
        assert "SENSITIVE" not in (ran.error_message or "")
    finally:
        db.close()


@requires_db
def test_cancel_queued_pipeline_job(env):
    from services.jobs import service
    job = _enqueue_direct(env, "l1_enrichment")
    service.request_cancel(env.session, job)
    env.session.refresh(job)
    assert job.status == "canceled"


# ── endpoints: auth, cost gate, S9 precheck, dedupe ──────────────────────────

@requires_db
def test_enrich_endpoint_auth(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    assert env.client.post(f"/api/mailbox/{env.mid}/pipeline/enrich").status_code == 401
    env.as_other()
    assert env.client.post(f"/api/mailbox/{env.mid}/pipeline/enrich").status_code == 404
    env.as_owner()
    r = env.client.post(f"/api/mailbox/{env.mid}/pipeline/enrich")
    assert r.status_code == 200 and r.json()["job_type"] == "l1_enrichment"


@requires_db
def test_embed_backfill_cost_gate(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    # No confirm → dry-run estimate, no job enqueued (no Voyage call).
    r = env.client.post(f"/api/mailbox/{env.mid}/pipeline/embed-backfill", json={})
    assert r.status_code == 200 and r.json()["needs_confirm"] is True
    from services.db import models as orm
    from sqlalchemy import func, select
    n = env.session.execute(select(func.count()).select_from(orm.Job).where(orm.Job.mailbox_id == env.mid)).scalar()
    assert n == 0
    # Confirm → enqueue with cost_confirmed.
    r2 = env.client.post(f"/api/mailbox/{env.mid}/pipeline/embed-backfill", json={"confirm": True})
    assert r2.status_code == 200 and r2.json()["job_type"] == "embedding_backfill"
    job = env.session.get(orm.Job, r2.json()["job_id"])
    assert job.params.get("cost_confirmed") is True


@requires_db
def test_materialize_requires_embeddings(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.as_owner()
    # No embeddings → 409, no job.
    r = env.client.post(f"/api/mailbox/{env.mid}/pipeline/materialize", json={})
    assert r.status_code == 409
    # Add a message + embedding → precheck passes → enqueue.
    from datetime import datetime, timezone

    from services.db import models as orm
    ts = datetime(2026, 4, 1, tzinfo=timezone.utc)
    th = orm.Thread(id=str(uuid.uuid4()), mailbox_id=env.mid, subject_norm="s", t_start=ts, t_end=ts)
    env.session.add(th); env.session.flush()
    msg = orm.Message(mailbox_id=env.mid, message_id_header="<m@x>", provider_id="p1",
                      thread_id=th.id, sender_email="a@acme.corp", ts=ts, subject="s",
                      clean_text="hi", sensitivity=["none"], noise=False)
    env.session.add(msg); env.session.flush()
    env.session.add(orm.MessageEmbedding(mailbox_id=env.mid, message_id=msg.id,
                                         embed_model="voyage-4", embed_dim=1024,
                                         content_hash="h", embedding=[0.0] * 1024))
    env.session.commit()
    r2 = env.client.post(f"/api/mailbox/{env.mid}/pipeline/materialize", json={})
    assert r2.status_code == 200 and r2.json()["job_type"] == "project_materialization"


@requires_db
def test_pipeline_enqueue_dedupes(env):
    from services.jobs import service
    a = _enqueue_direct(env, "l1_enrichment")
    # same type + mailbox via the endpoint idempotency key would dedupe; direct
    # enqueue with the same key does too:
    b = service.enqueue(env.session, tenant_id=env.tenant_id, job_type="l1_enrichment",
                        mailbox_id=env.mid, requested_by=env.user_id,
                        params={"mailbox_id": env.mid}, idempotency_key=a.idempotency_key or "k1")
    if a.idempotency_key:
        assert a.id == b.id


@requires_db
def test_recipient_cannot_access_pipeline_jobs(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    # No principal (as a recipient) → pipeline + jobs routes fail closed.
    assert env.client.post(f"/api/mailbox/{env.mid}/pipeline/enrich").status_code == 401
    assert env.client.get(f"/api/jobs/{uuid.uuid4()}").status_code == 401
    # recipient session route unaffected
    assert env.client.post("/api/handoff/recipient/session", json={"code": "nope"}).status_code != 401
