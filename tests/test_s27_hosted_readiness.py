"""S27 — hosted deployment readiness checks, startup guard, and /readyz.

All service-layer tests use injected fakes / crafted HostedEnv and never touch live
infrastructure. API tests drive FastAPI via TestClient. No live Voyage/Anthropic
call is made anywhere. A recurring assertion across tests: no secret, token, or DB
URL value leaks into a check message or an API response.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.hosted_readiness import (
    HostedEnv,
    HostedReadinessError,
    check_access_log_redaction,
    check_alembic_head,  # re-exported name (patched target lives here)
    check_auth_mode,
    check_cors_no_wildcard,
    check_cost_gates,
    check_database_url,
    check_deploy_env,
    check_frontend_origin,
    check_no_mailbox_id_bypass,
    check_oauth_config,
    check_oauth_redirect,
    check_recipient_snapshot_only,
    check_stuck_jobs,
    check_token_vault,
    check_worker_activity,
    check_worker_queue_observable,
    evaluate_readiness,
    gather_env,
    readiness_failed,
    run_hosted_checks,
    run_startup_guard,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _hosted_env(**over) -> HostedEnv:
    """A valid hosted-production env; override individual fields per test."""
    base = dict(
        auth_mode="production",
        deploy_env="production",
        allow_dev_vault=False,
        database_url="postgresql+psycopg2://u:p@db.internal:5432/ekc_prod",
        is_default_database_url=False,
        oauth_client_id="client-id-123",
        oauth_client_secret="client-secret-should-never-leak",
        oauth_redirect_uri="https://app.example.com/api/oauth/gmail/callback",
        allowed_origins="",
        frontend_api_base="",
    )
    base.update(over)
    return HostedEnv(**base)


def _fake_session(scalar=None, raises: bool = False):
    """Session stub whose execute(...).scalar() returns ``scalar`` (or raises)."""
    sess = MagicMock()
    if raises:
        sess.execute.side_effect = Exception("relation \"job\" does not exist")
    else:
        result = MagicMock()
        result.scalar.return_value = scalar
        sess.execute.return_value = result
    return sess


_GOOD = "postgresql+psycopg2://u:p@db.internal:5432/ekc_prod"


# ── Env snapshot + gate combinations (§9.1) ──────────────────────────────────

def test_gather_env_dev_no_deploy(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("EKC_DEPLOY_ENV", raising=False)
    env = gather_env()
    assert env.auth_mode == "dev"
    assert env.hosted_declared is False
    assert env.production_intent is False


def test_gather_env_production_intent_without_deploy(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.delenv("EKC_DEPLOY_ENV", raising=False)
    env = gather_env()
    assert env.hosted_declared is False       # not a fail-fast startup trigger
    assert env.production_intent is True       # but /readyz will evaluate + degrade


def test_gather_env_deploy_declared(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("EKC_DEPLOY_ENV", "production")
    env = gather_env()
    assert env.hosted_declared is True
    assert env.production_intent is True


# ── Auth-mode / deploy-env / bypass checks ───────────────────────────────────

def test_auth_mode_production_passes():
    assert check_auth_mode(_hosted_env(auth_mode="production")).status == "pass"


def test_auth_mode_dev_fails():
    c = check_auth_mode(_hosted_env(auth_mode="dev"))
    assert c.status == "fail" and "production" in c.message


def test_deploy_env_missing_fails():
    c = check_deploy_env(_hosted_env(deploy_env=""))
    assert c.status == "fail"


def test_deploy_env_production_passes():
    assert check_deploy_env(_hosted_env(deploy_env="production")).status == "pass"


def test_no_mailbox_id_bypass_fails_in_dev():
    c = check_no_mailbox_id_bypass(_hosted_env(auth_mode="dev"))
    assert c.status == "fail"


def test_no_mailbox_id_bypass_passes_in_production():
    assert check_no_mailbox_id_bypass(_hosted_env(auth_mode="production")).status == "pass"


# ── Token vault (§9.3) ───────────────────────────────────────────────────────

def test_dev_token_vault_blocked_in_hosted_production():
    c = check_token_vault(_hosted_env(), vault_is_dev=True)
    assert c.status == "fail" and "DevTokenVault" in c.message


def test_allow_dev_vault_env_blocked():
    c = check_token_vault(_hosted_env(allow_dev_vault=True), vault_is_dev=False)
    assert c.status == "fail" and "EKC_ALLOW_DEV_VAULT" in c.message


def test_production_vault_passes():
    assert check_token_vault(_hosted_env(), vault_is_dev=False).status == "pass"


def test_current_vault_is_dev_reflects_registry():
    """current_vault_is_dev is True with no/dev vault, False when a prod vault is set."""
    from services.oauth import vault as vaultmod

    original = vaultmod._vault
    try:
        vaultmod.set_vault(None)
        assert vaultmod.current_vault_is_dev() is True
        vaultmod.set_vault(MagicMock())  # a non-Dev vault stand-in
        assert vaultmod.current_vault_is_dev() is False
    finally:
        vaultmod.set_vault(original)


# ── DATABASE_URL (never echoes the URL) ──────────────────────────────────────

def test_database_url_missing_fails():
    c = check_database_url(_hosted_env(database_url="", is_default_database_url=False))
    assert c.status == "fail"


def test_database_url_dev_default_fails():
    c = check_database_url(_hosted_env(is_default_database_url=True))
    assert c.status == "fail" and "dev default" in c.message


def test_database_url_configured_passes_and_does_not_echo_value():
    c = check_database_url(_hosted_env(database_url=_GOOD))
    assert c.status == "pass"
    assert _GOOD not in c.message  # never echo the URL


# ── OAuth config + redirect ──────────────────────────────────────────────────

def test_oauth_config_missing_fails_without_leaking_secret():
    c = check_oauth_config(_hosted_env(oauth_client_secret=""))
    assert c.status == "fail"
    assert "client-secret-should-never-leak" not in c.message


def test_oauth_config_present_passes():
    assert check_oauth_config(_hosted_env()).status == "pass"


def test_oauth_redirect_localhost_rejected():
    c = check_oauth_redirect(_hosted_env(oauth_redirect_uri="http://127.0.0.1:8000/api/oauth/gmail/callback"))
    assert c.status == "fail" and "localhost" in c.message


def test_oauth_redirect_non_https_rejected():
    c = check_oauth_redirect(_hosted_env(oauth_redirect_uri="http://app.example.com/cb"))
    assert c.status == "fail"


def test_oauth_redirect_hosted_https_passes():
    assert check_oauth_redirect(_hosted_env()).status == "pass"


# ── Access-log redaction ─────────────────────────────────────────────────────

def test_access_log_redaction_installed_passes():
    assert check_access_log_redaction(True).status == "pass"


def test_access_log_redaction_missing_fails():
    assert check_access_log_redaction(False).status == "fail"


def test_is_redaction_installed_true_after_install():
    from services.api.log_redaction import install_access_log_redaction, is_redaction_installed

    install_access_log_redaction()
    assert is_redaction_installed() is True


# ── CORS + frontend origin (§9.4) ────────────────────────────────────────────

def test_cors_wildcard_rejected():
    c = check_cors_no_wildcard(_hosted_env(allowed_origins="*"))
    assert c.status == "fail"


def test_cors_wildcard_in_list_rejected():
    c = check_cors_no_wildcard(_hosted_env(allowed_origins="https://a.example.com, *"))
    assert c.status == "fail"


def test_cors_same_origin_default_passes():
    assert check_cors_no_wildcard(_hosted_env(allowed_origins="")).status == "pass"


def test_cors_explicit_allowlist_passes():
    assert check_cors_no_wildcard(_hosted_env(allowed_origins="https://app.example.com")).status == "pass"


def test_frontend_same_origin_preferred_passes():
    assert check_frontend_origin(_hosted_env()).status == "pass"


def test_frontend_cross_origin_without_base_warns():
    c = check_frontend_origin(_hosted_env(allowed_origins="https://app.example.com"))
    assert c.status == "warn" and c.failed is False


# ── Recipient snapshot-only static assertion ─────────────────────────────────

def test_recipient_router_is_snapshot_only():
    """The real recipient router must import no jobs/pipeline/principal symbol."""
    c = check_recipient_snapshot_only()
    assert c.status == "pass", c.message


# ── Worker / job-queue checks (migration-free, §9.2) ─────────────────────────

def test_worker_queue_observable_passes():
    assert check_worker_queue_observable(_fake_session()).status == "pass"


def test_worker_queue_unobservable_fails_safely():
    c = check_worker_queue_observable(_fake_session(raises=True))
    assert c.status == "fail"
    assert "Traceback" not in c.message
    assert "does not exist" not in c.message  # no raw DB error text


def test_worker_activity_recent_passes():
    now = datetime.now(timezone.utc)
    c = check_worker_activity(_fake_session(scalar=now - timedelta(seconds=30)), now=now)
    assert c.status == "pass"


def test_worker_activity_none_warns_not_fails():
    c = check_worker_activity(_fake_session(scalar=None))
    assert c.status == "warn" and c.failed is False


def test_worker_activity_stale_warns():
    now = datetime.now(timezone.utc)
    c = check_worker_activity(_fake_session(scalar=now - timedelta(hours=2)), now=now)
    assert c.status == "warn"


def test_stuck_jobs_zero_passes():
    assert check_stuck_jobs(_fake_session(scalar=0)).status == "pass"


def test_stuck_jobs_present_warns():
    c = check_stuck_jobs(_fake_session(scalar=3))
    assert c.status == "warn" and "3" in c.message


# ── Cost gates (info only; no cost governance built) ─────────────────────────

def test_cost_gates_info_default():
    c = check_cost_gates(_hosted_env(), getenv=lambda k, d="": d)
    assert c.status == "info" and c.failed is False


def test_cost_gates_kill_switch_reported():
    c = check_cost_gates(_hosted_env(), getenv=lambda k, d="": "1" if k == "EKC_EMBEDDING_KILL_SWITCH" else d)
    assert c.status == "info" and "kill switch" in c.message.lower()


# ── Aggregate: readiness + startup guard ─────────────────────────────────────

def _all_good_injected():
    return dict(
        engine=MagicMock(),
        session=_fake_session(scalar=0),
        vault_is_dev=False,
        redaction_installed=True,
        getenv=lambda k, d="": d,
    )


def test_run_hosted_checks_all_pass_when_good():
    with patch("services.hosted_readiness.check_database",
               return_value=_pass("database")), \
         patch("services.hosted_readiness.check_alembic_head",
               return_value=_pass("alembic_head")):
        checks = run_hosted_checks(_hosted_env(), **_all_good_injected())
    assert readiness_failed(checks) is False


def test_db_migration_mismatch_makes_not_ready():
    with patch("services.hosted_readiness.check_database", return_value=_pass("database")), \
         patch("services.hosted_readiness.check_alembic_head", return_value=_fail("alembic_head", "not at head")):
        ready, checks = evaluate_readiness(_hosted_env(), **_all_good_injected())
    assert ready is False
    assert any(c.name == "alembic_head" and c.failed for c in checks)


def test_evaluate_readiness_ready_when_all_pass():
    with patch("services.hosted_readiness.check_database", return_value=_pass("database")), \
         patch("services.hosted_readiness.check_alembic_head", return_value=_pass("alembic_head")):
        ready, checks = evaluate_readiness(_hosted_env(), **_all_good_injected())
    assert ready is True


def test_evaluate_readiness_dev_context_is_ready_without_db():
    """Local dev (no production intent) → ready, no checks, no DB work, no secrets."""
    ready, checks = evaluate_readiness(_hosted_env(auth_mode="dev", deploy_env=""))
    assert ready is True and checks == []


def test_production_intent_without_deploy_env_fails_readiness():
    """AUTH_MODE=production but EKC_DEPLOY_ENV missing → degraded (§9.1 rule 2)."""
    with patch("services.hosted_readiness.check_database", return_value=_pass("database")), \
         patch("services.hosted_readiness.check_alembic_head", return_value=_pass("alembic_head")):
        ready, checks = evaluate_readiness(_hosted_env(deploy_env=""), **_all_good_injected())
    assert ready is False
    assert any(c.name == "deploy_env" and c.failed for c in checks)


def test_startup_guard_noop_when_not_hosted_declared():
    """No EKC_DEPLOY_ENV=production → guard is a no-op (local dev / tests)."""
    out = run_startup_guard(env=_hosted_env(deploy_env="", auth_mode="dev"))
    assert out == []


def test_startup_guard_raises_on_unsafe_hosted_config():
    """EKC_DEPLOY_ENV=production + AUTH_MODE=dev must refuse to boot loudly."""
    with patch("services.hosted_readiness.check_database", return_value=_pass("database")), \
         patch("services.hosted_readiness.check_alembic_head", return_value=_pass("alembic_head")):
        with pytest.raises(HostedReadinessError) as ei:
            run_startup_guard(env=_hosted_env(auth_mode="dev"), **_all_good_injected())
    summary = ei.value.safe_summary()
    assert "auth_mode" in summary
    assert "client-secret-should-never-leak" not in summary  # no secret in the banner


def test_startup_guard_passes_on_good_hosted_config():
    with patch("services.hosted_readiness.check_database", return_value=_pass("database")), \
         patch("services.hosted_readiness.check_alembic_head", return_value=_pass("alembic_head")):
        out = run_startup_guard(env=_hosted_env(), **_all_good_injected())
    assert not readiness_failed(out)


# ── /readyz endpoint ─────────────────────────────────────────────────────────

def test_readyz_ready_in_dev(monkeypatch):
    """Local dev (conftest sets AUTH_MODE=dev, no deploy env) → 200 ready."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("EKC_DEPLOY_ENV", raising=False)
    from services.api.main import app

    client = TestClient(app)
    for path in ("/readyz", "/api/readyz"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json() == {"status": "ready"}


def test_readyz_degraded_does_not_leak_details():
    """503 body is a bare status — no check names, messages, or secrets."""
    from services.api.main import app
    from services.preflight import PreflightCheck

    leaky = PreflightCheck(
        "database_url", "fail",
        "DATABASE_URL is postgresql://secretuser:supersecretpw@host/db",
    )
    with patch("services.hosted_readiness.evaluate_readiness", return_value=(False, [leaky])):
        client = TestClient(app)
        r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded"}
    assert "supersecretpw" not in r.text
    assert "database_url" not in r.text


def test_readyz_evaluation_error_is_degraded_not_500():
    from services.api.main import app

    with patch("services.hosted_readiness.evaluate_readiness", side_effect=RuntimeError("boom")):
        client = TestClient(app)
        r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded"}


def test_healthz_still_works():
    """Liveness probes must not be affected by S27."""
    from services.api.main import app

    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/health").json() == {"status": "ok"}


# ── small local PreflightCheck builders (avoid importing at module top twice) ──

def _pass(name: str):
    from services.preflight import PreflightCheck
    return PreflightCheck(name, "pass", f"{name} ok")


def _fail(name: str, msg: str = "failed"):
    from services.preflight import PreflightCheck
    return PreflightCheck(name, "fail", msg)
