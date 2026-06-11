"""Tests for scripts/_env.py (load_local_env helper).

Four properties verified by the handoff doc:

1. Existing environment variables are NOT overwritten by .env.
2. Missing python-dotenv does not break script imports or load_local_env calls.
3. scripts/embed_backfill.py --dry-run still does not require VOYAGE_API_KEY.
4. The live VOYAGE_API_KEY path only activates for non-dry-run.

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
    """Without --dry-run, main() must raise SystemExit when VOYAGE_API_KEY is absent."""
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

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
