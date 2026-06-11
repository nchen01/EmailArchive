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
    header = "<atlas-1@acme.com>"
    hit = _make_hit(header, subject="Atlas cutover", snippet="Cutover completed on Friday.")
    synth_fn = _fake_synth_fn_with_header(header)

    result = _synthesize_l2_hits([hit], "what happened with atlas?", synth_fn=synth_fn, params=PARAMS)

    assert len(result.claims) == 1
    assert result.claims[0].source_message_ids == [header]


def test_l2_hits_drops_invalid_citation():
    """_synthesize_l2_hits: citation not in hit headers → allow-list drops it."""
    header = "<atlas-1@acme.com>"
    hit = _make_hit(header)
    synth_fn = _fake_synth_fn_invalid_citation("bogus-not-in-hits@x.com")

    result = _synthesize_l2_hits([hit], "query", synth_fn=synth_fn, params=PARAMS)

    assert result.claims == []


def test_l2_hits_context_includes_header_and_snippet():
    """_synthesize_l2_hits: the context string passed to synth_fn includes hit metadata."""
    header = "<thread-42@example.com>"
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
    h1 = "<msg-001@acme.com>"
    h2 = "<msg-002@acme.com>"
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
    header = "<l2-only@example.com>"
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
    header = "<real@example.com>"
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
