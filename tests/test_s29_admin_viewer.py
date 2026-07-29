"""S29 — read-only Admin / Audit Viewer (DB-gated).

Implements docs/s28-admin-audit-ops-plan.md (read set). Verifies role gating,
tenant isolation, security-reviewer masking, and — the core of the suite — that
NO forbidden content/secret ever appears in an admin response: evidence bodies,
claim text, package reason, raw job params/error_message, sync tokens, vault refs,
provider email (for reviewers), or unsafe audit-metadata keys. Also confirms the
recipient snapshot-only invariant is untouched.

All principals are injected via app.dependency_overrides; AUTH_MODE-independent
(the override replaces get_principal entirely) except the explicit 401 test.
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

# Sentinel values that must NEVER appear in an admin response.
EVIDENCE_BODY = "SENTINEL-evidence-body-zzz"
CLAIM_TEXT = "SENTINEL-claim-text-zzz"
JOB_PARAM_SECRET = "SENTINEL-job-param-secret"
JOB_ERR_MSG = "SENTINEL-raw-error-message"
SYNC_TOKEN = "SENTINEL-sync-token"
VAULT_REF = "SENTINEL-vault-ref"
PROVIDER_EMAIL = "connected.mailbox@gmail.com"
RECIPIENT_EMAIL = "coverage.person@partner.example"
AUDIT_BLOCKED_BODY = "SENTINEL-audit-metadata-body"
EXCL_TARGET_REF = "SENTINEL-excluded-thread-id"

_ALL_SENTINELS = [
    EVIDENCE_BODY, CLAIM_TEXT, JOB_PARAM_SECRET, JOB_ERR_MSG,
    SYNC_TOKEN, VAULT_REF, AUDIT_BLOCKED_BODY, EXCL_TARGET_REF,
]


def _mk_tenant(session, name):
    from services.db import models as orm
    t = orm.Tenant(name="S29-" + name + "-" + uuid.uuid4().hex[:8])
    session.add(t)
    session.flush()
    return t


def _mk_user(session, tenant, email, role):
    from services.db import models as orm
    u = orm.AppUser(tenant_id=tenant.id, idp_subject="s29-" + uuid.uuid4().hex[:10], email=email)
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
        reason="vacation", title="Q3 coverage", policy_mode="standard", version=1,
        lineage_id=lineage, published_at=datetime.now(timezone.utc),
    )
    s.add(pkg)
    s.flush()
    pid = str(pkg.id)

    s.add(orm.HandoffRecipient(
        package_id=pid, recipient_email=RECIPIENT_EMAIL,
        capability_code_hash="SENTINEL-code-hash-" + uuid.uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    ))
    s.add(orm.HandoffEvidence(
        package_id=pid, message_id_header="SENTINEL-msgid", subject="SENTINEL-subject",
        sender_display="Someone", sender_domain="acme.corp", body_snapshot=EVIDENCE_BODY,
    ))
    s.add(orm.HandoffClaim(
        package_id=pid, kind="briefing", text=CLAIM_TEXT,
        source_message_id_headers=["SENTINEL-source-header"], confidence=0.9,
    ))
    s.add(orm.HandoffExclusion(
        package_id=pid, exclusion_type="sensitivity", target_type="thread",
        target_ref=EXCL_TARGET_REF, aggregate_label="sensitive_legal",
    ))
    s.add(orm.HandoffAuditEvent(
        package_id=pid, lineage_id=lineage, actor="owner:creator@acme.corp",
        action="handoff.published", metadata_={"stage": "published", "body": AUDIT_BLOCKED_BODY},
    ))
    job = orm.Job(
        tenant_id=t.id, mailbox_id=mid, job_type="embedding_backfill", status="failed",
        params={"secret_param": JOB_PARAM_SECRET, "mailbox_id": mid},
        progress={"processed": 5, "total": 10, "phase": "embedding", "note": "SENTINEL-progress-note"},
        summary="5 of 10 embedded", error_category="voyage_error", error_message=JOB_ERR_MSG,
        attempt=1, max_attempts=3, worker_id="SENTINEL-host:1234",
    )
    s.add(job)
    s.flush()
    jid = str(job.id)
    s.add(orm.MailboxProviderAccount(
        tenant_id=t.id, owner_user_id=u_creator.id, mailbox_id=mid, provider="gmail",
        provider_account_email=PROVIDER_EMAIL, vault_ref=VAULT_REF,
        scopes_granted=["https://www.googleapis.com/auth/gmail.readonly"], status="connected",
    ))
    s.add(orm.AuditLog(
        mailbox_id=mid, actor="creator@acme.corp", action="ingest", scope="window",
        message_count=42, started_at=datetime.now(timezone.utc), sync_token=SYNC_TOKEN,
    ))
    s.commit()

    client = TestClient(app)
    ns = SimpleNamespace(
        client=client, app=app, session=s, get_principal=get_principal, mid=mid, pid=pid, jid=jid,
        admin=_principal(t, u_admin, "admin@acme.corp", {"admin"}),
        reviewer=_principal(t, u_reviewer, "reviewer@acme.corp", {"security_reviewer"}),
        creator=_principal(t, u_creator, "creator@acme.corp", {"creator"}),
        other_admin=_principal(t2, u_other_admin, "admin@rival.corp", {"admin"}),
    )
    try:
        yield ns
    finally:
        app.dependency_overrides.clear()
        pkg_ids = select(orm.HandoffPackage.id).where(orm.HandoffPackage.mailbox_id == mid)
        s.execute(orm.HandoffAuditEvent.__table__.delete().where(orm.HandoffAuditEvent.package_id.in_(pkg_ids)))
        s.execute(orm.HandoffPackage.__table__.delete().where(orm.HandoffPackage.mailbox_id == mid))  # cascades children
        s.execute(orm.Job.__table__.delete().where(orm.Job.tenant_id.in_([t.id, t2.id])))
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


# ── Role gating ───────────────────────────────────────────────────────────────

@requires_db
def test_admin_can_read_all_endpoints(env):
    _as(env, env.admin)
    c = env.client
    assert c.get("/api/admin/overview").status_code == 200
    assert c.get("/api/admin/packages").status_code == 200
    assert c.get(f"/api/admin/packages/{env.pid}").status_code == 200
    assert c.get(f"/api/admin/packages/{env.pid}/audit").status_code == 200
    assert c.get("/api/admin/provider-accounts").status_code == 200
    assert c.get("/api/admin/jobs").status_code == 200
    assert c.get(f"/api/admin/jobs/{env.jid}").status_code == 200
    assert c.get("/api/admin/audit").status_code == 200
    assert c.get("/api/admin/exclusions/summary").status_code == 200
    assert c.get("/api/admin/readiness").status_code == 200


@requires_db
def test_reviewer_can_read_shared_endpoints_but_not_admin_only(env):
    _as(env, env.reviewer)
    c = env.client
    assert c.get("/api/admin/packages").status_code == 200
    assert c.get("/api/admin/jobs").status_code == 200
    assert c.get("/api/admin/exclusions/summary").status_code == 200
    # admin-only routes are denied to a reviewer-only principal
    assert c.get("/api/admin/overview").status_code == 403
    assert c.get("/api/admin/readiness").status_code == 403


@requires_db
def test_creator_cannot_access_admin_routes(env):
    _as(env, env.creator)
    for path in ("/api/admin/overview", "/api/admin/packages", f"/api/admin/packages/{env.pid}",
                 "/api/admin/jobs", "/api/admin/provider-accounts", "/api/admin/audit",
                 "/api/admin/exclusions/summary"):
        assert env.client.get(path).status_code == 403, path


@requires_db
def test_unauthenticated_production_is_401(env, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    env.app.dependency_overrides.clear()  # no principal source → fail closed
    assert env.client.get("/api/admin/overview").status_code == 401
    assert env.client.get("/api/admin/packages").status_code == 401


# ── Tenant isolation ──────────────────────────────────────────────────────────

@requires_db
def test_cross_tenant_isolation(env):
    _as(env, env.other_admin)  # admin of a DIFFERENT tenant
    c = env.client
    assert c.get("/api/admin/packages").json() == []       # no rows from tenant T
    assert c.get("/api/admin/jobs").json() == []
    assert c.get(f"/api/admin/packages/{env.pid}").status_code == 404
    assert c.get(f"/api/admin/packages/{env.pid}/audit").status_code == 404
    assert c.get(f"/api/admin/jobs/{env.jid}").status_code == 404


# ── Security-reviewer masking ─────────────────────────────────────────────────

@requires_db
def test_admin_sees_full_recipient_email_reviewer_sees_masked(env):
    _as(env, env.admin)
    admin_pkgs = env.client.get("/api/admin/packages").json()
    assert any(p["recipient_email"] == RECIPIENT_EMAIL for p in admin_pkgs)

    _as(env, env.reviewer)
    rev_pkgs = env.client.get("/api/admin/packages").json()
    rec = next(p["recipient_email"] for p in rev_pkgs if p["id"] == env.pid)
    assert rec != RECIPIENT_EMAIL and "***@" in rec and rec.endswith("partner.example")


@requires_db
def test_reviewer_provider_view_hides_email_and_scopes(env):
    _as(env, env.reviewer)
    accts = env.client.get("/api/admin/provider-accounts").json()
    a = next(x for x in accts if x["mailbox_id"] == env.mid)
    assert a["provider_account_email"] is None
    assert a["scopes_granted"] == []
    assert a["status"] == "connected"  # status/timestamps still visible

    _as(env, env.admin)
    accts = env.client.get("/api/admin/provider-accounts").json()
    a = next(x for x in accts if x["mailbox_id"] == env.mid)
    assert a["provider_account_email"] == PROVIDER_EMAIL
    assert a["scopes_granted"]  # admin sees scopes


# ── No forbidden fields / no leakage (the core suite) ─────────────────────────

@requires_db
def test_no_sentinel_content_leaks_in_any_admin_response(env):
    _as(env, env.admin)
    c = env.client
    texts = [
        c.get("/api/admin/overview").text,
        c.get("/api/admin/packages").text,
        c.get(f"/api/admin/packages/{env.pid}").text,
        c.get(f"/api/admin/packages/{env.pid}/audit").text,
        c.get("/api/admin/provider-accounts").text,
        c.get("/api/admin/jobs").text,
        c.get(f"/api/admin/jobs/{env.jid}").text,
        c.get("/api/admin/audit").text,
        c.get("/api/admin/exclusions/summary").text,
        c.get("/api/admin/readiness").text,
    ]
    blob = "\n".join(texts)
    for sentinel in _ALL_SENTINELS:
        assert sentinel not in blob, f"leaked: {sentinel}"
    # readiness must not echo the test DB password either
    assert "ekc_dev_password" not in blob


@requires_db
def test_job_admin_view_omits_unsafe_fields(env):
    _as(env, env.admin)
    job = env.client.get(f"/api/admin/jobs/{env.jid}").json()
    for forbidden in ("params", "error_message", "idempotency_key", "worker_id"):
        assert forbidden not in job, forbidden
    assert job["error_category"] == "voyage_error"
    # progress_safe keeps numeric counters + whitelisted phase; drops arbitrary strings
    ps = job["progress_safe"]
    assert ps.get("processed") == 5 and ps.get("total") == 10 and ps.get("phase") == "embedding"
    assert "note" not in ps


@requires_db
def test_package_detail_excludes_reason_and_content(env):
    _as(env, env.admin)
    d = env.client.get(f"/api/admin/packages/{env.pid}").json()
    assert "reason" not in d                       # no raw free-text `reason` field
    assert d["reason_category"] == "vacation"      # safe structured enum only (§18.2)
    assert d["claim_count"] == 1 and d["evidence_count"] == 1  # counts only, no bodies
    assert d["recipient_state"] == "granted"


@requires_db
def test_audit_metadata_projection_drops_unsafe_keys(env):
    _as(env, env.admin)
    events = env.client.get(f"/api/admin/packages/{env.pid}/audit").json()
    ev = next(e for e in events if e["action"] == "handoff.published")
    assert ev["safe_metadata"].get("stage") == "published"   # safe key kept
    assert "body" not in ev["safe_metadata"]                  # blocked key dropped
    assert AUDIT_BLOCKED_BODY not in str(ev)


@requires_db
def test_audit_log_view_omits_sync_token(env):
    _as(env, env.admin)
    rows = env.client.get("/api/admin/audit").json()
    assert rows and all("sync_token" not in r for r in rows)
    row = next(r for r in rows if r["mailbox_id"] == env.mid)
    assert row["action"] == "ingest" and row["message_count"] == 42


@requires_db
def test_exclusions_summary_is_counts_only(env):
    _as(env, env.admin)
    summ = env.client.get("/api/admin/exclusions/summary").json()
    assert summ["total_excluded"] == 1
    assert summ["by_type"][0]["exclusion_type"] == "sensitivity"
    assert EXCL_TARGET_REF not in env.client.get("/api/admin/exclusions/summary").text


# ── Sensitive-read audit + recipient invariant ────────────────────────────────

@requires_db
def test_package_detail_read_writes_admin_audit_event(env):
    from services.db import models as orm
    from sqlalchemy import select
    _as(env, env.admin)
    env.client.get(f"/api/admin/packages/{env.pid}")
    env.session.expire_all()
    actions = env.session.execute(
        select(orm.HandoffAuditEvent.action).where(orm.HandoffAuditEvent.package_id == env.pid)
    ).scalars().all()
    assert "admin.package.viewed" in actions


@requires_db
def test_recipient_snapshot_only_invariant_untouched(env):
    # The S27 static assertion still passes: recipient router imports nothing from
    # jobs/pipeline/principal/admin.
    from services.hosted_readiness import check_recipient_snapshot_only
    assert check_recipient_snapshot_only().status == "pass"
    # The recipient session route is not blocked by the admin/auth layer.
    r = env.client.post("/api/handoff/recipient/session", json={"code": "not-a-real-code"})
    assert r.status_code != 401
