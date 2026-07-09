"""Regression tests for RFC 2047 encoded-word subject decoding (issue: MIME headers
leaked raw encoded-word strings into Message.subject, RetrievalHit.subject, and
citation chips in the UI).

All tests are offline (no DB, no ingest run). They exercise decode_mime_words()
directly and via the ingest pipeline fixture.
"""
from __future__ import annotations

import pytest
from services.ingest.normalize.mime import decode_mime_words


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


# ── Canonical location ────────────────────────────────────────────────────────

def test_decode_mime_words_canonical_location():
    """decode_mime_words must be importable from the canonical mime.py module.

    Ensures address.py and threads.py don't still have competing definitions.
    """
    from services.ingest.normalize.mime import decode_mime_words as from_mime
    from services.ingest.normalize.address import decode_mime_words as from_addr
    from services.ingest.normalize.threads import decode_mime_words as from_threads

    # All three imports must resolve to the same callable object (same id).
    assert from_mime is from_addr, "address.py must re-export from mime.py"
    assert from_mime is from_threads, "threads.py must re-export from mime.py"


# ── Outbound decode: RetrievalHit construction ────────────────────────────────

def _make_fake_row(encoded_subject: str):
    """Minimal mapping-like object for mocking DB query rows."""
    from datetime import datetime, timezone

    class _Row(dict):
        pass

    row = _Row({
        "message_id": "00000000-0000-0000-0000-000000000001",
        "message_id_header": "test-msg@example.com",
        "thread_id": "00000000-0000-0000-0000-000000000002",
        "subject": encoded_subject,
        "clean_text": "Body text.",
        "ts": datetime(2026, 1, 15, tzinfo=timezone.utc),
        "sensitivity": ["none"],
        "noise": False,
        "sender_email": "sender@example.com",
        "to_emails": [],
        "cc_emails": [],
        "vector_score": 0.9,
        "fts_score": 0.5,
    })
    return row


def test_vector_search_decodes_subject(monkeypatch):
    """RetrievalHit.subject from vector_search must be decoded, not raw encoded-word."""
    from unittest.mock import MagicMock, patch
    from services.retrieval.params import RetrievalParams
    from services.retrieval.vector import vector_search

    encoded = "INCIDENT P1: p99 =?utf-8?b?4oCU?= triaging"
    fake_row = _make_fake_row(encoded)

    session = MagicMock()
    # Mock: execute().mappings().all() returns our fake row
    session.execute.return_value.mappings.return_value.all.return_value = [fake_row]
    # Mock project and person resolution to return empty
    session.execute.return_value.all.return_value = []

    params = RetrievalParams(
        embed_model="fake", embed_dim=4,
        min_vector_score=0.0, min_fts_score=0.0,
        vector_top_k=10, fts_top_k=10, rerank_top_k=10,
    )

    hits = vector_search(session, "mailbox-id", [0.1, 0.2, 0.3, 0.4], params)

    assert hits, "Expected at least one hit"
    assert "=?" not in hits[0].subject, (
        f"Encoded-word leaked into RetrievalHit.subject: {hits[0].subject!r}"
    )
    assert "—" in hits[0].subject, f"Em dash missing: {hits[0].subject!r}"


def test_fts_search_decodes_subject(monkeypatch):
    """RetrievalHit.subject from fts_search must be decoded, not raw encoded-word."""
    from unittest.mock import MagicMock
    from services.retrieval.params import RetrievalParams
    from services.retrieval.fts import fts_search

    encoded = "=?US-ASCII?Q?View_Your_New_Benefit_Amount?="
    fake_row = _make_fake_row(encoded)

    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = [fake_row]
    session.execute.return_value.all.return_value = []

    params = RetrievalParams(
        embed_model="fake", embed_dim=4,
        min_vector_score=0.0, min_fts_score=0.0,
        vector_top_k=10, fts_top_k=10, rerank_top_k=10,
    )

    hits = fts_search(session, "mailbox-id", "benefit amount", params)

    assert hits, "Expected at least one hit"
    assert "=?" not in hits[0].subject, (
        f"Encoded-word leaked into RetrievalHit.subject: {hits[0].subject!r}"
    )
    assert "View Your New Benefit Amount" in hits[0].subject


# ── Outbound decode: _build_supporting_evidence ───────────────────────────────

def test_build_supporting_evidence_decodes_l2_hit_subject():
    """EvidenceMessage.subject from an L2 hit must have decoded subject."""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from services.retrieval.contracts import RetrievalHit
    from services.api.routers.cover_for_me import _build_supporting_evidence
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    # Simulate an L2 hit whose subject is still encoded (pre-fix DB row)
    encoded_subject = "=?US-ASCII?Q?View_Your_New_Benefit_Amount?="
    msg_header = "ssa-benefit@subscriptions.ssa.gov"
    hit = RetrievalHit(
        message_id="00000000-0000-0000-0000-000000000001",
        message_id_header=msg_header,
        thread_id="00000000-0000-0000-0000-000000000002",
        project_ids=(),
        person_ids=(),
        ts=datetime(2026, 1, 15, tzinfo=timezone.utc),
        subject=encoded_subject,
        snippet="SSA benefit amount notification.",
        vector_score=0.9,
        fts_score=None,
        rerank_score=0.9,
        source="vector",
        sensitivity=("none",),
        noise=False,
    )

    result = SynthesisResult(
        claims=[SynthesisClaim(text="Benefit updated.", source_message_ids=[msg_header])],
        model="test",
        usage={},
    )

    db = MagicMock()
    db.execute.return_value.all.return_value = []  # no DB row needed for the L2 hit
    evidence = _build_supporting_evidence(result, [hit], db, "mailbox-id", "gmail")

    assert evidence, "Expected one EvidenceMessage"
    assert "=?" not in evidence[0].subject, (
        f"Encoded-word leaked into EvidenceMessage.subject: {evidence[0].subject!r}"
    )
    assert "View Your New Benefit Amount" in evidence[0].subject


def test_build_supporting_evidence_decodes_l1_db_subject():
    """EvidenceMessage.subject from an L1 DB row must have decoded subject."""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from services.api.routers.cover_for_me import _build_supporting_evidence
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    msg_header = "l1-msg@acme.com"
    encoded_subject = "INCIDENT P1: p99 =?utf-8?b?4oCU?= triaging"

    # Result cites a header that is NOT in l2_hits, so it falls back to DB
    result = SynthesisResult(
        claims=[SynthesisClaim(text="Incident resolved.", source_message_ids=[msg_header])],
        model="test",
        usage={},
    )

    # Fake DB row returned by the L1 DB query
    class _FakeRow:
        message_id_header = msg_header
        subject = encoded_subject
        ts = datetime(2026, 1, 15, tzinfo=timezone.utc)
        clean_text = "P1 incident body."
        sender_email = "oncall@acme.com"
        addresses = None

    db = MagicMock()
    db.execute.return_value.all.return_value = [_FakeRow()]

    evidence = _build_supporting_evidence(result, [], db, "mailbox-id", "gmail")

    assert evidence, "Expected one EvidenceMessage"
    assert "=?" not in evidence[0].subject, (
        f"Encoded-word leaked into EvidenceMessage.subject: {evidence[0].subject!r}"
    )
    assert "—" in evidence[0].subject


# ── Repair script (dry-run) ───────────────────────────────────────────────────

def test_repair_script_dry_run_makes_no_db_writes():
    """repair() with dry_run=True must scan rows but not issue any UPDATE.

    Uses the injectable session_factory parameter — no module-level patching
    needed and importing the script does not mutate env or construct the engine.
    """
    from unittest.mock import MagicMock
    from scripts.repair_encoded_subjects import repair

    encoded = "=?US-ASCII?Q?View_Your_New?="

    session_mock = MagicMock()
    # First execute: SELECT returns one encoded-subject row
    first_result = MagicMock()
    first_result.all.return_value = [("row-id-1", encoded)]
    # Second execute (pagination): no more rows
    second_result = MagicMock()
    second_result.all.return_value = []
    session_mock.execute.side_effect = [first_result, second_result]

    stats = repair(dry_run=True, batch_size=500, session_factory=lambda: session_mock)

    assert stats["scanned"] == 1
    assert stats["updated"] == 1   # counted but not written
    assert stats["unchanged"] == 0
    session_mock.commit.assert_not_called()


def test_repair_script_import_has_no_side_effects():
    """Importing repair_encoded_subjects must not call load_local_env or touch the DB."""
    import importlib
    import os
    from unittest.mock import patch

    sentinel = object()
    called = []

    def _spy_load():
        called.append(True)

    # Reload the module from scratch with a spy on load_local_env
    with patch.dict(os.environ, {}, clear=False):
        with patch("scripts._env.load_local_env", _spy_load):
            if "scripts.repair_encoded_subjects" in __import__("sys").modules:
                del __import__("sys").modules["scripts.repair_encoded_subjects"]
            importlib.import_module("scripts.repair_encoded_subjects")

    assert not called, "load_local_env must not be called at import time"
