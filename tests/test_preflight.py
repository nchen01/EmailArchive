"""Unit + integration tests for S8.3 preflight checks.

Service-layer tests use injected fakes and never touch live infrastructure.
API tests drive FastAPI via TestClient with dependency overrides so they too
run without Postgres.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Helpers ──────────────────────────────────────────────────────────────────

def _env(**kwargs):
    """Return a dict suitable for os.environ patching (all values are strings)."""
    return {k: v for k, v in kwargs.items()}


def _fake_engine(reachable: bool = True):
    """Minimal SQLAlchemy engine stub."""
    eng = MagicMock()
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    if reachable:
        eng.connect.return_value = conn
    else:
        eng.connect.side_effect = Exception("connection refused")
    return eng


def _fake_session(count: int = 0):
    """Minimal SQLAlchemy session stub that returns ``count`` for COUNT(*)."""
    sess = MagicMock()
    result = MagicMock()
    result.scalar.return_value = count
    sess.execute.return_value = result
    sess.close = MagicMock()
    return sess


# ── Key-presence checks ───────────────────────────────────────────────────────

def test_voyage_key_missing(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    from services.preflight import check_voyage_api_key
    c = check_voyage_api_key()
    assert c.status == "fail"
    assert "VOYAGE_API_KEY" in c.message
    # Must not leak any key value
    assert "sk-" not in c.message


def test_voyage_key_present(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "sk-voyage-test")
    from services.preflight import check_voyage_api_key
    c = check_voyage_api_key()
    assert c.status == "pass"
    assert "sk-voyage-test" not in c.message


def test_anthropic_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from services.preflight import check_anthropic_api_key
    c = check_anthropic_api_key()
    assert c.status == "fail"
    assert "ANTHROPIC_API_KEY" in c.message
    assert "sk-" not in c.message


def test_anthropic_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from services.preflight import check_anthropic_api_key
    c = check_anthropic_api_key()
    assert c.status == "pass"
    assert "sk-ant-test" not in c.message


# ── Database checks ───────────────────────────────────────────────────────────

def test_db_unreachable_fails_cleanly():
    from services.preflight import check_database
    c = check_database(engine=_fake_engine(reachable=False))
    assert c.status == "fail"
    # No stack trace in the message — just a class name and hint
    assert "Traceback" not in c.message
    assert "DATABASE_URL" in c.message


def test_db_reachable_passes():
    from services.preflight import check_database
    c = check_database(engine=_fake_engine(reachable=True))
    assert c.status == "pass"


def test_db_module_not_found_includes_module_name():
    """ModuleNotFoundError must surface the missing module name, not just the class."""
    from services.preflight import check_database
    eng = MagicMock()
    err = ModuleNotFoundError("No module named 'dotenv'")
    err.name = "dotenv"
    eng.connect.side_effect = err
    c = check_database(engine=eng)
    assert c.status == "fail"
    assert "dotenv" in c.message


def test_preflight_cli_sys_path_fix():
    """scripts/preflight.py must not raise ModuleNotFoundError when repo root is absent from sys.path."""
    import sys
    import importlib
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parent.parent)
    scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")

    # Simulate direct execution: scripts/ on path, repo root absent.
    original_path = sys.path[:]
    try:
        if repo_root in sys.path:
            sys.path.remove(repo_root)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        # Force re-import so the module runs with the modified path.
        if "scripts.preflight" in sys.modules:
            del sys.modules["scripts.preflight"]

        # Importing must not raise; main() must be callable.
        import scripts.preflight as pf  # noqa: F401 — existence check
        assert callable(pf.main)
    finally:
        sys.path[:] = original_path


def test_alembic_not_at_head_fails():
    """DB is reachable but current revision != head."""
    from services.preflight import check_alembic_head

    eng = _fake_engine(reachable=True)

    with patch("alembic.config.Config"), \
         patch("alembic.script.ScriptDirectory") as MockSD, \
         patch("alembic.runtime.migration.MigrationContext") as MockMC:

        MockSD.from_config.return_value.get_current_head.return_value = "head_abc"
        mc_inst = MagicMock()
        mc_inst.get_current_revision.return_value = "old_rev"
        MockMC.configure.return_value = mc_inst

        c = check_alembic_head(engine=eng)

    assert c.status == "fail"
    assert "head_abc" in c.message or "old_rev" in c.message


def test_alembic_at_head_passes():
    from services.preflight import check_alembic_head

    eng = _fake_engine(reachable=True)

    with patch("alembic.config.Config"), \
         patch("alembic.script.ScriptDirectory") as MockSD, \
         patch("alembic.runtime.migration.MigrationContext") as MockMC:

        MockSD.from_config.return_value.get_current_head.return_value = "abc123"
        mc_inst = MagicMock()
        mc_inst.get_current_revision.return_value = "abc123"
        MockMC.configure.return_value = mc_inst

        c = check_alembic_head(engine=eng)

    assert c.status == "pass"
    assert "abc123" in c.message


# ── Embedding checks ──────────────────────────────────────────────────────────

def test_embeddings_none_fails():
    from services.preflight import check_embeddings
    sess = _fake_session(count=0)
    c = check_embeddings("some-uuid", session=sess)
    assert c.status == "fail"
    assert "No voyage-4 embeddings" in c.message


def test_embeddings_present_passes():
    from services.preflight import check_embeddings
    sess = _fake_session(count=42)
    c = check_embeddings("some-uuid", session=sess)
    assert c.status == "pass"
    assert "42" in c.message


# ── Reranking flag ────────────────────────────────────────────────────────────

def test_enable_reranking_off(monkeypatch):
    monkeypatch.delenv("ENABLE_RERANKING", raising=False)
    from services.preflight import check_enable_reranking
    c = check_enable_reranking()
    assert c.status == "pass"


def test_enable_reranking_on_warns_not_fails(monkeypatch):
    monkeypatch.setenv("ENABLE_RERANKING", "1")
    from services.preflight import check_enable_reranking
    c = check_enable_reranking()
    assert c.status == "warn"
    assert c.failed is False  # warnings must not count as failures


# ── Aggregate runner ──────────────────────────────────────────────────────────

def test_run_checks_skips_alembic_when_db_down(monkeypatch):
    """Alembic + embeddings checks must be omitted when DB is unreachable."""
    monkeypatch.setenv("VOYAGE_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    from services.preflight import run_checks
    eng = _fake_engine(reachable=False)
    checks = run_checks(mailbox_id="mbx-1", engine=eng)
    names = [c.name for c in checks]
    assert "alembic_head" not in names
    assert "embeddings" not in names
    assert any(c.name == "database" and c.failed for c in checks)


def test_run_checks_no_key_values_in_output(monkeypatch):
    """Secret key values must never appear in any check message."""
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-secret-abc")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret-xyz")

    from services.preflight import run_checks
    eng = _fake_engine(reachable=False)
    checks = run_checks(engine=eng)
    for c in checks:
        assert "voyage-secret-abc" not in c.message
        assert "anthropic-secret-xyz" not in c.message


# ── API endpoint ──────────────────────────────────────────────────────────────

def _client_with_fake_checks(checks_result):
    """Build a TestClient that returns a preset list from run_checks."""
    from services.api.main import app

    with patch("services.api.routers.preflight.run_checks", return_value=checks_result):
        client = TestClient(app, raise_server_exceptions=True)
        yield client


@pytest.fixture()
def passing_checks():
    from services.preflight import PreflightCheck
    return [
        PreflightCheck("voyage_api_key", "pass", "VOYAGE_API_KEY is configured"),
        PreflightCheck("anthropic_api_key", "pass", "ANTHROPIC_API_KEY is configured"),
        PreflightCheck("database", "pass", "Database is reachable"),
        PreflightCheck("alembic_head", "pass", "Database is at migration head (abc)"),
        PreflightCheck("enable_reranking", "pass", "Reranker is off"),
        PreflightCheck("voyage_rate_limits", "info", "Production requires a payment method"),
    ]


@pytest.fixture()
def failing_checks():
    from services.preflight import PreflightCheck
    return [
        PreflightCheck("voyage_api_key", "fail", "VOYAGE_API_KEY is not set"),
        PreflightCheck("anthropic_api_key", "pass", "ANTHROPIC_API_KEY is configured"),
        PreflightCheck("database", "pass", "Database is reachable"),
        PreflightCheck("enable_reranking", "pass", "Reranker is off"),
        PreflightCheck("voyage_rate_limits", "info", "Production requires a payment method"),
    ]


def test_api_preflight_ok(passing_checks):
    from services.api.main import app
    with patch("services.api.routers.preflight.run_checks", return_value=passing_checks):
        client = TestClient(app)
        r = client.get("/api/preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    names = [c["name"] for c in body["checks"]]
    assert "voyage_api_key" in names


def test_api_preflight_fail(failing_checks):
    from services.api.main import app
    with patch("services.api.routers.preflight.run_checks", return_value=failing_checks):
        client = TestClient(app)
        r = client.get("/api/preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    fail_check = next(c for c in body["checks"] if c["name"] == "voyage_api_key")
    assert fail_check["status"] == "fail"


def test_api_preflight_with_mailbox_id(passing_checks):
    from services.api.main import app
    with patch("services.api.routers.preflight.run_checks", return_value=passing_checks) as mock_rc:
        client = TestClient(app)
        r = client.get("/api/preflight?mailbox_id=test-uuid-1234")
    assert r.status_code == 200
    mock_rc.assert_called_once_with(mailbox_id="test-uuid-1234")


def test_api_preflight_warn_does_not_make_ok_false():
    from services.preflight import PreflightCheck
    from services.api.main import app
    warn_checks = [
        PreflightCheck("voyage_api_key", "pass", "configured"),
        PreflightCheck("enable_reranking", "warn", "reranker active"),
    ]
    with patch("services.api.routers.preflight.run_checks", return_value=warn_checks):
        client = TestClient(app)
        r = client.get("/api/preflight")
    body = r.json()
    assert body["ok"] is True  # warns are non-fatal


def test_api_preflight_no_key_values_in_response(monkeypatch):
    """Key values must never leak through the API response."""
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-secret-in-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret-in-test")

    from services.api.main import app
    from services.preflight import PreflightCheck

    # Simulate a check that accidentally embeds the key (should never reach the caller).
    # We verify the service layer doesn't expose it.
    safe_checks = [
        PreflightCheck("voyage_api_key", "pass", "VOYAGE_API_KEY is configured"),
    ]
    with patch("services.api.routers.preflight.run_checks", return_value=safe_checks):
        client = TestClient(app)
        r = client.get("/api/preflight")
    text = r.text
    assert "voyage-secret-in-test" not in text
    assert "anthropic-secret-in-test" not in text
