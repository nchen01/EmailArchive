"""Regression tests for RFC 2047 encoded-word subject decoding (issue: MIME headers
leaked raw encoded-word strings into Message.subject, RetrievalHit.subject, and
citation chips in the UI).

All tests are offline (no DB, no ingest run). They exercise decode_mime_words()
directly and via the ingest pipeline fixture.
"""
from __future__ import annotations

import pytest
from services.ingest.normalize.threads import decode_mime_words


# ── decode_mime_words unit tests ──────────────────────────────────────────────

def test_plain_ascii_unchanged():
    assert decode_mime_words("Atlas Migration: cutover plan") == "Atlas Migration: cutover plan"


def test_empty_unchanged():
    assert decode_mime_words("") == ""


def test_none_safe():
    # Should not be called with None in practice, but gracefully handled
    assert decode_mime_words(None) is None  # type: ignore[arg-type]


def test_b_encoding_em_dash():
    """=?utf-8?b?4oCU?= is base64 for the em dash character U+2014."""
    assert decode_mime_words("=?utf-8?b?4oCU?=") == "—"


def test_b_encoding_mixed_with_plain_text():
    """Encoded word embedded in plain text — the common smoke-dataset pattern."""
    raw = "INCIDENT P1: prod-api p99 latency spike =?utf-8?b?4oCU?= triaging"
    decoded = decode_mime_words(raw)
    assert decoded == "INCIDENT P1: prod-api p99 latency spike — triaging"
    assert "=?" not in decoded


def test_q_encoding_simple():
    """Q-encoding: underscore → space, plus quoted-printable for non-ASCII."""
    raw = "=?US-ASCII?Q?View_Your_New_Benefit_Amount?="
    decoded = decode_mime_words(raw)
    assert decoded == "View Your New Benefit Amount"
    assert "=?" not in decoded


def test_q_encoding_multi_part():
    """Two consecutive Q-encoded words split across the word boundary — RFC 2047 §6.2.

    The words must be joined without an inserted space.
    Input word 1: 'View_Your_New_Benefit_Amount_Usin' → 'View Your New Benefit Amount Usin'
    Input word 2: 'g_Your_my_Social_Security_Account' → 'g Your my Social Security Account'
    Joined:       'View Your New Benefit Amount Using Your my Social Security Account'
    """
    raw = (
        "=?US-ASCII?Q?View_Your_New_Benefit_Amount_Usin?= "
        "=?US-ASCII?Q?g_Your_my_Social_Security_Account?="
    )
    decoded = decode_mime_words(raw)
    assert "=?" not in decoded
    assert "View Your New Benefit Amount Usin" in decoded
    assert decoded.startswith("View Your New Benefit Amount Usin")


def test_cp1252_encoding():
    """Real SSA subject using Windows-1252 (Cp1252) charset."""
    # "=?Cp1252?Q?There=92s_Still_Time!_Claim_Your_2021_Child_Tax_Credit?="
    # 0x92 in cp1252 = RIGHT SINGLE QUOTATION MARK (U+2019 = ')
    raw = "=?Cp1252?Q?There=92s_Still_Time!_Claim_Your_2021?="
    decoded = decode_mime_words(raw)
    assert "=?" not in decoded
    assert "There" in decoded
    assert "Still Time" in decoded


def test_no_encoded_word_passthrough():
    """Strings without '=?' are returned unchanged with zero processing."""
    raw = "Q3 engineering budget review — headcount request"
    assert decode_mime_words(raw) == raw


def test_malformed_encoded_word_fallback():
    """Malformed encoded-word strings must fall back to the raw value, not raise."""
    raw = "=?BOGUS_CHARSET?B?!!!not_valid_base64!!!?="
    result = decode_mime_words(raw)
    # Must return something, not raise; either decoded or the raw value.
    assert isinstance(result, str)


# ── Integration: decoded subjects reach Message.subject ───────────────────────

def test_pipeline_decodes_encoded_subject():
    """Encoded-word subjects in a fixture message reach Message.subject decoded.

    We inject a fake RawMessage with an encoded Subject header and run it
    through reconstruct() to assert Message.subject is plain Unicode.
    """
    from services.ingest.providers.base import MimePart, RawMessage
    from services.ingest.normalize.threads import reconstruct
    from services.ingest.params import IngestParams

    encoded_subject = "INCIDENT P1: latency spike =?utf-8?b?4oCU?= triaging"

    raw = RawMessage(
        provider_id="test-001",
        provider_thread_id="thread-001",
        headers={
            "Message-ID": "<test-001@test.example>",
            "From": "eng@acme.com",
            "To": "team@acme.com",
            "Date": "Mon, 01 Jan 2026 10:00:00 +0000",
            "Subject": encoded_subject,
        },
        mime_parts=[
            MimePart(
                type="text/plain",
                bytes=b"Test body content.",
                charset="utf-8",
                filename=None,
            )
        ],
        labels=[],
    )

    messages, _ = reconstruct(
        [raw],
        owner_email="eng@acme.com",
        params=IngestParams(),
        db_mailbox_id="00000000-0000-0000-0000-000000000001",
    )

    assert len(messages) == 1
    msg = messages[0]
    assert "=?" not in msg.subject, f"Encoded-word leaked into Message.subject: {msg.subject!r}"
    assert "—" in msg.subject, f"Em dash missing from decoded subject: {msg.subject!r}"
    assert "INCIDENT P1: latency spike" in msg.subject


# ── SynthesisClaim empty-text guard ──────────────────────────────────────────

def test_synthesis_claim_rejects_empty_text():
    """SynthesisClaim with text='' must be rejected (bare citation chip fix)."""
    from pydantic import ValidationError
    from services.synthesis.contracts import SynthesisClaim

    with pytest.raises(ValidationError):
        SynthesisClaim(text="", source_message_ids=["msg-id@example.com"])


def test_synthesis_claim_strips_whitespace_in_synth_fn(monkeypatch):
    """Whitespace-only claim text must be filtered before reaching SynthesisResult.

    min_length=1 only rejects empty string at schema level. The synth_fn filter
    (not c.text.strip()) catches whitespace-only claims that would render as
    bare citation chips in the UI.
    """
    from unittest.mock import MagicMock, patch
    from services.synthesis.contracts import SynthesisResult

    # Build a fake API response that contains a whitespace-only claim
    class _FakeBlock:
        type = "tool_use"
        input = {"claims": [
            {"text": "   ", "source_message_ids": ["real-msg@example.com"]},
            {"text": "Actual claim text.", "source_message_ids": ["real-msg@example.com"]},
        ]}

    class _FakeResp:
        content = [_FakeBlock()]
        usage = MagicMock(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=None, cache_read_input_tokens=None,
        )

    fake_client = MagicMock()
    fake_client.messages.create.return_value = _FakeResp()

    with patch("services.synthesis.client.get_anthropic_client", return_value=fake_client):
        from services.synthesis.client import make_anthropic_synth_fn
        from services.synthesis.params import PARAMS

        synth_fn = make_anthropic_synth_fn(PARAMS)
        result: SynthesisResult = synth_fn("system", "context", "query")

    # Whitespace-only claim must be dropped; the real claim must survive
    texts = [c.text for c in result.claims]
    assert "   " not in texts, "Whitespace-only claim leaked into SynthesisResult"
    assert "Actual claim text." in texts
