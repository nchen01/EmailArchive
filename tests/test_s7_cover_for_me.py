"""S7.11 cover-for-me L2 upgrade — offline unit tests.

Exercises the new _synthesize_l2_hits function and the L2-only path through
synthesize_cover_for_me. All tests are DB-free and use FakeEmbedClient /
deterministic fake synth_fn so they run in CI without Postgres or Voyage AI.

These tests complement the DB-gated S5 tests in test_cover_for_me.py, which
prove the original S5 behaviors still work.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.retrieval.contracts import RetrievalHit
from services.synthesis.contracts import SynthesisClaim, SynthesisResult
from services.synthesis.cover_for_me import _synthesize_l2_hits, synthesize_cover_for_me
from services.synthesis.params import PARAMS

_TS = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _make_hit(header: str, subject: str = "Test subject", snippet: str = "Snippet text") -> RetrievalHit:
    return RetrievalHit(
        message_id="00000000-0000-0000-0000-000000000001",
        message_id_header=header,
        thread_id="00000000-0000-0000-0000-000000000002",
        project_ids=(),
        person_ids=(),
        ts=_TS,
        subject=subject,
        snippet=snippet,
        vector_score=0.85,
        fts_score=None,
        rerank_score=0.85,
        source="vector",
        sensitivity=("none",),
        noise=False,
    )


def _fake_synth_fn_with_header(header: str):
    """Returns a synth_fn that always emits one claim citing ``header``."""
    def _fn(system: str, context: str, query: str) -> SynthesisResult:
        return SynthesisResult(
            claims=[SynthesisClaim(text="Evidence found.", source_message_ids=[header])],
            model="fake",
            usage={},
        )
    return _fn


def _fake_synth_fn_invalid_citation(header: str = "bogus@example.com"):
    """Returns a synth_fn that emits a claim citing a header not in any hit."""
    def _fn(system: str, context: str, query: str) -> SynthesisResult:
        return SynthesisResult(
            claims=[SynthesisClaim(text="Fabricated.", source_message_ids=[header])],
            model="fake",
            usage={},
        )
    return _fn


# ── _synthesize_l2_hits unit tests ───────────────────────────────────────────

def test_l2_hits_returns_cited_claim():
    """_synthesize_l2_hits: fake synth returns a valid citation → claim is returned."""
    header = "atlas-1@acme.com"
    hit = _make_hit(header, subject="Atlas cutover", snippet="Cutover completed on Friday.")
    synth_fn = _fake_synth_fn_with_header(header)

    result = _synthesize_l2_hits([hit], "what happened with atlas?", synth_fn=synth_fn, params=PARAMS)

    assert len(result.claims) == 1
    assert result.claims[0].source_message_ids == [header]


def test_l2_hits_drops_invalid_citation():
    """_synthesize_l2_hits: citation not in hit headers → allow-list drops it."""
    header = "atlas-1@acme.com"
    hit = _make_hit(header)
    synth_fn = _fake_synth_fn_invalid_citation("bogus-not-in-hits@x.com")

    result = _synthesize_l2_hits([hit], "query", synth_fn=synth_fn, params=PARAMS)

    assert result.claims == []


def test_l2_hits_context_includes_header_and_snippet():
    """_synthesize_l2_hits: the context string passed to synth_fn includes hit metadata."""
    header = "thread-42@example.com"
    snippet = "Important finding about the project."
    hit = _make_hit(header, subject="Project status", snippet=snippet)

    captured: dict = {}

    def _capturing_fn(system: str, context: str, query: str) -> SynthesisResult:
        captured["context"] = context
        return SynthesisResult(
            claims=[SynthesisClaim(text="x", source_message_ids=[header])],
            model="fake",
            usage={},
        )

    _synthesize_l2_hits([hit], "query", synth_fn=_capturing_fn, params=PARAMS)

    assert header in captured["context"]
    assert snippet in captured["context"]
    assert "Project status" in captured["context"]


def test_l2_hits_multiple_hits_all_in_allow_list():
    """Multiple L2 hits: each cited header is allowed; uncited ones are dropped."""
    h1 = "msg-001@acme.com"
    h2 = "msg-002@acme.com"
    hits = [_make_hit(h1, subject="First"), _make_hit(h2, subject="Second")]

    def _fn(system: str, context: str, query: str) -> SynthesisResult:
        return SynthesisResult(
            claims=[
                SynthesisClaim(text="First finding.", source_message_ids=[h1]),
                SynthesisClaim(text="Fabricated finding.", source_message_ids=["not-in-hits@x"]),
            ],
            model="fake",
            usage={},
        )

    result = _synthesize_l2_hits(hits, "query", synth_fn=_fn, params=PARAMS)

    assert len(result.claims) == 1
    assert result.claims[0].source_message_ids == [h1]


# ── synthesize_cover_for_me L2-only path (DB-free) ───────────────────────────

def test_synthesize_cfm_l2_only_returns_cited_result():
    """L2-only path: no L1 match, l2_hits present → _synthesize_l2_hits is called."""
    header = "l2-only@example.com"
    hits = [_make_hit(header, subject="L2 evidence")]
    synth_fn = _fake_synth_fn_with_header(header)

    result, routed_to = synthesize_cover_for_me(
        "what about the widget deployment?",
        None,
        None,
        db=None,
        mailbox_id="unused",
        synth_fn=synth_fn,
        l2_hits=hits,
    )

    assert routed_to is None
    assert len(result.claims) == 1
    assert result.claims[0].source_message_ids == [header]


def test_synthesize_cfm_l2_only_drops_invalid_citations():
    """L2-only path: fake synth cites a header not in any hit → filtered out."""
    header = "real@example.com"
    hits = [_make_hit(header)]
    synth_fn = _fake_synth_fn_invalid_citation("invented@fake.com")

    result, routed_to = synthesize_cover_for_me(
        "some query",
        None,
        None,
        db=None,
        mailbox_id="unused",
        synth_fn=synth_fn,
        l2_hits=hits,
    )

    assert routed_to is None
    assert result.claims == []


def test_synthesize_cfm_both_empty_returns_insufficient():
    """No L1 match and no L2 hits → insufficient evidence, no model call."""
    no_model_call_flag: list[bool] = []

    def _should_not_be_called(system, context, query):
        no_model_call_flag.append(True)
        return SynthesisResult(claims=[], model="fake", usage={})

    result, routed_to = synthesize_cover_for_me(
        "unknowable query",
        None,
        None,
        db=None,
        mailbox_id="unused",
        synth_fn=_should_not_be_called,
        l2_hits=None,
    )

    assert routed_to is None
    assert "insufficient" in (result.state or "").lower()
    assert no_model_call_flag == [], "synth_fn must not be called on insufficient-evidence path"


def test_synthesize_cfm_l2_empty_list_returns_insufficient():
    """Empty l2_hits list (not None) is treated the same as None — insufficient evidence."""
    result, routed_to = synthesize_cover_for_me(
        "another unknowable query",
        None,
        None,
        db=None,
        mailbox_id="unused",
        synth_fn=None,
        l2_hits=[],
    )

    assert routed_to is None
    assert "insufficient" in (result.state or "").lower()


# ── S8.2/S8.4 unit tests ─────────────────────────────────────────────────────

def test_run_l2_returns_status_tuple_no_key(monkeypatch):
    """_run_l2 with no embed_client and absent VOYAGE_API_KEY → disabled_no_key."""
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    from services.api.routers.cover_for_me import _run_l2
    status, hits = _run_l2("query", None, "mailbox-id", None)
    assert status == "disabled_no_key"
    assert hits == []


def test_run_l2_returns_unavailable_when_key_set_but_client_none(monkeypatch):
    """_run_l2 with no embed_client but key present → unavailable (construction failed)."""
    monkeypatch.setenv("VOYAGE_API_KEY", "fake-key")
    from services.api.routers.cover_for_me import _run_l2
    status, hits = _run_l2("query", None, "mailbox-id", None)
    assert status == "unavailable"
    assert hits == []


def test_run_l2_rate_limit_returns_degraded():
    """_run_l2 when embed_query raises EmbedError wrapping RateLimitError → degraded_rate_limit."""
    from services.retrieval.embed_client import EmbedError
    from services.api.routers.cover_for_me import _run_l2

    class _RateLimitClient:
        def embed_query(self, text):
            raise EmbedError("Voyage API call failed (RateLimitError)")

    status, hits = _run_l2("query", _RateLimitClient(), "mailbox-id", None)
    assert status == "degraded_rate_limit"
    assert hits == []


def test_build_supporting_evidence_only_cited_headers():
    """_build_supporting_evidence includes only headers that appear in claims."""
    from unittest.mock import MagicMock
    from services.api.routers.cover_for_me import _build_supporting_evidence
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    cited_header = "cited@example.com"
    uncited_header = "uncited@example.com"

    result = SynthesisResult(
        claims=[SynthesisClaim(text="Claim.", source_message_ids=[cited_header])],
        model="fake", usage={},
    )
    cited_hit = _make_hit(cited_header, subject="Cited Subject", snippet="Cited snippet.")
    uncited_hit = _make_hit(uncited_header, subject="Uncited Subject", snippet="Uncited snippet.")

    evidence = _build_supporting_evidence(result, [cited_hit, uncited_hit], MagicMock(), "mailbox")

    headers = [e.message_id_header for e in evidence]
    assert cited_header in headers
    assert uncited_header not in headers, "Uncited hit must not appear in supporting_evidence"


def test_build_supporting_evidence_uses_hit_metadata():
    """_build_supporting_evidence uses l2 hit subject/snippet directly (no DB call)."""
    from unittest.mock import MagicMock
    from services.api.routers.cover_for_me import _build_supporting_evidence
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    header = "hit@example.com"
    hit = _make_hit(header, subject="Hit Subject", snippet="Hit snippet text.")
    result = SynthesisResult(
        claims=[SynthesisClaim(text="Claim.", source_message_ids=[header])],
        model="fake", usage={},
    )

    db_mock = MagicMock()
    db_mock.execute.side_effect = AssertionError("DB should not be queried for L2-sourced headers")

    evidence = _build_supporting_evidence(result, [hit], db_mock, "mailbox")

    assert len(evidence) == 1
    assert evidence[0].subject == "Hit Subject"
    assert evidence[0].snippet == "Hit snippet text."


def test_build_supporting_evidence_empty_claims():
    """_build_supporting_evidence with no claims returns empty list."""
    from unittest.mock import MagicMock
    from services.api.routers.cover_for_me import _build_supporting_evidence
    from services.synthesis.contracts import SynthesisResult

    result = SynthesisResult(claims=[], model="fake", usage={})
    evidence = _build_supporting_evidence(result, [], MagicMock(), "mailbox")
    assert evidence == []


def test_build_supporting_evidence_mixed_l1_l2_preserves_claim_order():
    """Mixed L1/L2 citations in one claim preserve first-seen citation order.

    Claim cites [l1-header, l2-header].  L1 requires a DB lookup; L2 is in
    l2_hits.  supporting_evidence must return [l1-entry, l2-entry], not
    all-L2-first then all-L1.
    """
    from unittest.mock import MagicMock
    from services.api.routers.cover_for_me import _build_supporting_evidence
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    l1_header = "l1msg@example.com"
    l2_header = "l2msg@example.com"

    result = SynthesisResult(
        claims=[SynthesisClaim(
            text="Mixed claim.",
            source_message_ids=[l1_header, l2_header],  # L1 first
        )],
        model="fake", usage={},
    )
    l2_hit = _make_hit(l2_header, subject="L2 Subject")

    # Simulate a DB row for the L1 header
    l1_row = MagicMock()
    l1_row.message_id_header = l1_header
    l1_row.subject = "L1 Subject"
    l1_row.ts = _TS
    l1_row.clean_text = "L1 snippet."

    db_mock = MagicMock()
    db_mock.execute.return_value.all.return_value = [l1_row]

    evidence = _build_supporting_evidence(result, [l2_hit], db_mock, "mailbox")

    assert len(evidence) == 2
    assert evidence[0].message_id_header == l1_header, "L1 must come first (matches claim order)"
    assert evidence[1].message_id_header == l2_header, "L2 must come second"
