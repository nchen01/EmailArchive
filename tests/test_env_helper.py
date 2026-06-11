"""Tests for scripts/_env.py (load_local_env helper).

Four properties verified by the handoff doc, plus two regression guards:

1. Existing environment variables are NOT overwritten by .env.
2. Missing python-dotenv does not break script imports or load_local_env calls.
3. scripts/embed_backfill.py --dry-run still does not require VOYAGE_API_KEY.
4. The live VOYAGE_API_KEY path only activates for non-dry-run.
5. (Regression) scripts/dev_seed.py, scripts/gmail_smoke_ingest.py, and
   scripts/fill_smoke_labels.py do NOT set DATABASE_URL at import time —
   prevents the setdefault anti-pattern from silently returning and blocking
   .env from taking effect.

All tests are offline (no DB, no Voyage API).
"""
from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import patch, MagicMock

import pytest


# ── 1. Existing env vars are not overwritten ──────────────────────────────────

def test_load_local_env_calls_load_dotenv_with_override_false():
    """load_local_env must pass override=False to dotenv.load_dotenv.

    override=False is the guarantee that existing process env vars (set by CI
    or the shell) are never silently overwritten by a stale .env file.
    """
    captured: list[dict] = []

    def spy(**kwargs):
        captured.append(kwargs)

    # Patch dotenv.load_dotenv at the module level so the `from dotenv import`
    # inside load_local_env picks up the spy. No module reload needed.
    with patch("dotenv.load_dotenv", spy):
        from scripts._env import load_local_env
        load_local_env()

    assert captured, "load_dotenv was not called"
    assert captured[0].get("override") is False, (
        f"Expected override=False, got: {captured[0]}"
    )


def test_existing_env_var_not_overwritten_by_dotenv(monkeypatch):
    """A variable already set in the process env must survive load_local_env()."""
    monkeypatch.setenv("EKC_TEST_SENTINEL", "original")

    # Make dotenv's load_dotenv a real no-op but verify override=False is passed.
    with patch("dotenv.load_dotenv") as mock_ld:
        from scripts._env import load_local_env
        load_local_env()
        # Check call kwargs — override=False is what prevents clobbering.
        call_kwargs = mock_ld.call_args[1] if mock_ld.call_args else {}
        assert call_kwargs.get("override") is False

    assert os.environ["EKC_TEST_SENTINEL"] == "original"


# ── 2. Missing python-dotenv does not break imports or calls ──────────────────

def test_load_local_env_is_noop_when_dotenv_missing(monkeypatch):
    """ImportError from dotenv must be swallowed; load_local_env returns None."""
    # Setting sys.modules["dotenv"] = None makes `from dotenv import x` raise
    # ImportError, simulating dotenv not being installed.
    monkeypatch.setitem(sys.modules, "dotenv", None)

    from scripts._env import load_local_env
    result = load_local_env()   # must not raise
    assert result is None


def test_embed_backfill_imports_without_dotenv():
    """Importing embed_backfill must succeed even if dotenv is not installed.

    embed_backfill only calls load_local_env() inside main(), never at module
    import time, so this import must always be safe.
    """
    # embed_backfill is already imported by the test runner; the key assertion is
    # that no top-level `import dotenv` exists in the module.
    import scripts.embed_backfill as bf
    assert callable(bf.main)
    assert callable(bf.run_backfill)


# ── 3. --dry-run never requires VOYAGE_API_KEY ────────────────────────────────

def test_dry_run_does_not_need_voyage_key(monkeypatch):
    """embed_backfill main() with --dry-run must succeed without VOYAGE_API_KEY."""
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    import scripts.embed_backfill as mod

    calls: list[dict] = []

    def fake_run_backfill(**kwargs):
        calls.append(kwargs)
        return {
            "total_messages": 0, "already_embedded": 0, "to_embed": 0,
            "est_tokens": 0, "est_cost_usd": 0.0, "model": "voyage-4", "embedded": 0,
        }

    monkeypatch.setattr(mod, "run_backfill", fake_run_backfill)

    mod.main(["--mailbox-id", str(uuid.uuid4()), "--dry-run", "--confirm"])

    assert calls, "run_backfill should have been called"
    from scripts.embed_backfill import _DryRunEmbedClient
    assert isinstance(calls[0]["embed_client"], _DryRunEmbedClient), (
        "--dry-run must use _DryRunEmbedClient, not VoyageEmbedClient"
    )


# ── 4. Live key path only activates for non-dry-run ──────────────────────────

def test_non_dry_run_requires_voyage_key(monkeypatch):
    """Without --dry-run, main() must raise SystemExit when VOYAGE_API_KEY is absent.

    load_local_env is patched to a no-op so it cannot re-load the key from .env
    (a real .env may contain the key; without the patch, delenv has no effect).
    """
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    import scripts._env as _env_mod
    monkeypatch.setattr(_env_mod, "load_local_env", lambda: None)

    import scripts.embed_backfill as mod

    with pytest.raises(SystemExit):
        mod.main(["--mailbox-id", str(uuid.uuid4()), "--confirm"])


def test_voyage_client_constructed_for_non_dry_run(monkeypatch):
    """Without --dry-run and with a key, _build_voyage_client must be called."""
    monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-for-test")

    import scripts.embed_backfill as mod

    constructed: list[str] = []

    class _FakeVoyageClient:
        model = "voyage-4"
        dim   = 1024

    def fake_build(model: str):
        constructed.append(model)
        return _FakeVoyageClient()

    calls: list[dict] = []

    def fake_run_backfill(**kwargs):
        calls.append(kwargs)
        return {
            "total_messages": 0, "already_embedded": 0, "to_embed": 0,
            "est_tokens": 0, "est_cost_usd": 0.0, "model": "voyage-4", "embedded": 0,
        }

    monkeypatch.setattr(mod, "_build_voyage_client", fake_build)
    monkeypatch.setattr(mod, "run_backfill", fake_run_backfill)

    mod.main(["--mailbox-id", str(uuid.uuid4()), "--confirm"])

    assert constructed, "_build_voyage_client must be called for non-dry-run"
    assert not isinstance(
        calls[0]["embed_client"], mod._DryRunEmbedClient
    ), "non-dry-run must not use _DryRunEmbedClient"


# ── 5. Regression: no import-time DATABASE_URL mutation in CLI scripts ────────
#
# Both dev_seed.py and gmail_smoke_ingest.py previously called
# os.environ.setdefault("DATABASE_URL", ...) at module level, which silently
# blocked load_dotenv(override=False) from ever populating that key from .env.
# These tests lock in the fix so the same pattern cannot silently return.

def test_dev_seed_does_not_set_database_url_at_import(monkeypatch):
    """Importing dev_seed must not write DATABASE_URL into the environment."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Importing dev_seed triggers several heavy service imports; patch
    # create_engine to avoid actually connecting to a database.
    with patch("sqlalchemy.create_engine", return_value=MagicMock()):
        import importlib
        import scripts.dev_seed  # noqa: F401 — side-effect check only
        importlib.invalidate_caches()

    assert "DATABASE_URL" not in os.environ, (
        "scripts/dev_seed.py must not call os.environ.setdefault('DATABASE_URL', ...) "
        "at import time — that prevents .env from being loaded by load_local_env()"
    )


def test_gmail_smoke_ingest_does_not_set_database_url_at_import(monkeypatch):
    """Importing gmail_smoke_ingest must not write DATABASE_URL into the environment."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with patch("sqlalchemy.create_engine", return_value=MagicMock()):
        import importlib
        import scripts.gmail_smoke_ingest  # noqa: F401 — side-effect check only
        importlib.invalidate_caches()

    assert "DATABASE_URL" not in os.environ, (
        "scripts/gmail_smoke_ingest.py must not call os.environ.setdefault('DATABASE_URL', ...) "
        "at import time — that prevents .env from being loaded by load_local_env()"
    )


def test_fill_smoke_labels_does_not_set_database_url_at_import(monkeypatch):
    """Importing fill_smoke_labels must not write DATABASE_URL into the environment."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with patch("sqlalchemy.create_engine", return_value=MagicMock()):
        import importlib
        import scripts.fill_smoke_labels  # noqa: F401 — side-effect check only
        importlib.invalidate_caches()

    assert "DATABASE_URL" not in os.environ, (
        "scripts/fill_smoke_labels.py must not call os.environ.setdefault('DATABASE_URL', ...) "
        "at import time — that prevents .env from being loaded by load_local_env()"
    )
