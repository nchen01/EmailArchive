"""Unit tests for services/synthesis/client.py (offline, no Anthropic key needed).

Covers:
- ANTHROPIC_MODEL env override respected at synth_fn construction time.
- ANTHROPIC_DISABLE_CACHE_CONTROL=1 removes cache_control from the request.
- Flat tool input_schema contains no $ref or $defs (Anthropic API rejects them).
- Provider error message appears in the log; key values never appear.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


# ── Schema shape ──────────────────────────────────────────────────────────────

def test_emit_synthesis_schema_has_no_ref():
    """_EMIT_SYNTHESIS_INPUT_SCHEMA must be a flat dict with no $ref or $defs.

    Anthropic's tools API does not support JSON Schema $ref in input_schema.
    Sending Pydantic's model_json_schema() output (which uses $defs/$ref for
    nested models) causes HTTP 400 'invalid_request_error'.
    """
    from services.synthesis.client import _EMIT_SYNTHESIS_INPUT_SCHEMA

    def _walk(obj, path=""):
        if isinstance(obj, dict):
            assert "$ref" not in obj, f"$ref found at {path}"
            assert "$defs" not in obj, f"$defs found at {path}"
            for k, v in obj.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _walk(v, f"{path}[{i}]")

    _walk(_EMIT_SYNTHESIS_INPUT_SCHEMA)


def test_emit_synthesis_schema_has_claims_array():
    """Schema must declare a 'claims' array of objects with text and source_message_ids."""
    from services.synthesis.client import _EMIT_SYNTHESIS_INPUT_SCHEMA

    props = _EMIT_SYNTHESIS_INPUT_SCHEMA["properties"]
    assert "claims" in props
    items = props["claims"]["items"]
    assert items["type"] == "object"
    assert "text" in items["properties"]
    assert "source_message_ids" in items["properties"]
    assert items["properties"]["source_message_ids"]["items"]["type"] == "string"


# ── ANTHROPIC_MODEL env override ──────────────────────────────────────────────

def test_model_env_override_used(monkeypatch):
    """ANTHROPIC_MODEL env var overrides params.model at synth_fn construction."""
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-override-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")

    captured = {}

    def _fake_create(**kwargs):
        captured["model"] = kwargs.get("model")
        raise RuntimeError("stop after capture")

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _fake_create

    with patch("services.synthesis.client.get_anthropic_client", return_value=fake_client):
        from services.synthesis.client import make_anthropic_synth_fn
        from services.synthesis.params import PARAMS

        synth_fn = make_anthropic_synth_fn(PARAMS)
        with pytest.raises(RuntimeError, match="stop after capture"):
            synth_fn("system", "context", "query")

    assert captured.get("model") == "claude-override-model"


def test_model_default_when_no_env(monkeypatch):
    """Without ANTHROPIC_MODEL, the model comes from params.model."""
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")

    captured = {}

    def _fake_create(**kwargs):
        captured["model"] = kwargs.get("model")
        raise RuntimeError("stop after capture")

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _fake_create

    with patch("services.synthesis.client.get_anthropic_client", return_value=fake_client):
        from services.synthesis.client import make_anthropic_synth_fn
        from services.synthesis.params import PARAMS

        synth_fn = make_anthropic_synth_fn(PARAMS)
        with pytest.raises(RuntimeError, match="stop after capture"):
            synth_fn("system", "context", "query")

    assert captured.get("model") == PARAMS.model


# ── cache_control flag ────────────────────────────────────────────────────────

def test_cache_control_present_by_default(monkeypatch):
    """cache_control must appear on the context block unless explicitly disabled."""
    monkeypatch.delenv("ANTHROPIC_DISABLE_CACHE_CONTROL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")

    captured = {}

    def _fake_create(**kwargs):
        captured["messages"] = kwargs.get("messages")
        raise RuntimeError("stop after capture")

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _fake_create

    with patch("services.synthesis.client.get_anthropic_client", return_value=fake_client):
        from services.synthesis.client import make_anthropic_synth_fn
        from services.synthesis.params import PARAMS

        synth_fn = make_anthropic_synth_fn(PARAMS)
        with pytest.raises(RuntimeError):
            synth_fn("system", "context text", "query")

    content = captured["messages"][0]["content"]
    context_block = content[0]
    assert "cache_control" in context_block, "cache_control missing from context block by default"
    assert context_block["cache_control"] == {"type": "ephemeral"}


def test_cache_control_disabled_by_env(monkeypatch):
    """ANTHROPIC_DISABLE_CACHE_CONTROL=1 removes cache_control from context block."""
    monkeypatch.setenv("ANTHROPIC_DISABLE_CACHE_CONTROL", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")

    captured = {}

    def _fake_create(**kwargs):
        captured["messages"] = kwargs.get("messages")
        raise RuntimeError("stop after capture")

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _fake_create

    with patch("services.synthesis.client.get_anthropic_client", return_value=fake_client):
        from services.synthesis.client import make_anthropic_synth_fn
        from services.synthesis.params import PARAMS

        synth_fn = make_anthropic_synth_fn(PARAMS)
        with pytest.raises(RuntimeError):
            synth_fn("system", "context text", "query")

    content = captured["messages"][0]["content"]
    context_block = content[0]
    assert "cache_control" not in context_block, (
        "cache_control should be absent when ANTHROPIC_DISABLE_CACHE_CONTROL=1"
    )


# ── Error logging ─────────────────────────────────────────────────────────────

def test_provider_error_message_logged(monkeypatch, caplog):
    """Provider error message (not our content) must appear in the error log."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")

    class _FakeBadRequest(Exception):
        status_code = 400
        message = "model not found: claude-invalid"

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _FakeBadRequest()

    with patch("services.synthesis.client.get_anthropic_client", return_value=fake_client):
        from services.synthesis.client import make_anthropic_synth_fn
        from services.synthesis.params import PARAMS

        synth_fn = make_anthropic_synth_fn(PARAMS)
        with caplog.at_level(logging.ERROR, logger="services.synthesis.client"):
            with pytest.raises(_FakeBadRequest):
                synth_fn("system", "context", "query")

    assert any("model not found" in r.message for r in caplog.records), (
        "Provider error message must appear in log output"
    )
    assert any("status=400" in r.message for r in caplog.records), (
        "HTTP status must appear in log output"
    )


def test_provider_error_log_excludes_key_and_content(monkeypatch, caplog):
    """Secret key values and prompt content must never appear in error log."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-key-never-log-this")

    class _FakeError(Exception):
        status_code = 429
        message = "rate limit exceeded"

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _FakeError()

    with patch("services.synthesis.client.get_anthropic_client", return_value=fake_client):
        from services.synthesis.client import make_anthropic_synth_fn
        from services.synthesis.params import PARAMS

        synth_fn = make_anthropic_synth_fn(PARAMS)
        secret_context = "SENSITIVE_CONTEXT_TEXT_NEVER_LOG"
        with caplog.at_level(logging.ERROR, logger="services.synthesis.client"):
            with pytest.raises(_FakeError):
                synth_fn("system", secret_context, "query")

    log_text = " ".join(r.message for r in caplog.records)
    assert "sk-ant-secret-key-never-log-this" not in log_text, "Key must not appear in logs"
    assert "SENSITIVE_CONTEXT_TEXT_NEVER_LOG" not in log_text, "Context must not appear in logs"
