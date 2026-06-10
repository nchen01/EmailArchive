"""S7.9 — RetrievalHit and InsufficientEvidence contract tests (offline)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.retrieval.contracts import InsufficientEvidence, RetrievalHit

_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_hit(**overrides) -> RetrievalHit:
    defaults = dict(
        message_id="aaaa-bbbb",
        message_id_header="<msg@example.com>",
        thread_id="cccc-dddd",
        project_ids=(),
        person_ids=(),
        ts=_NOW,
        subject="Test subject",
        snippet="First 300 chars...",
        vector_score=0.85,
        fts_score=None,
        rerank_score=0.85,
        source="vector",
        sensitivity=("none",),
        noise=False,
    )
    defaults.update(overrides)
    return RetrievalHit(**defaults)


# ── RetrievalHit — shape and immutability ────────────────────────────────────

def test_retrieval_hit_constructs():
    hit = _make_hit()
    assert hit.message_id == "aaaa-bbbb"
    assert hit.source == "vector"
    assert hit.vector_score == 0.85
    assert hit.fts_score is None


def test_retrieval_hit_is_frozen():
    hit = _make_hit()
    with pytest.raises((AttributeError, TypeError)):
        hit.message_id = "other"  # type: ignore[misc]


def test_retrieval_hit_is_hashable():
    hit = _make_hit()
    s = {hit}
    assert hit in s


def test_retrieval_hit_equality():
    h1 = _make_hit()
    h2 = _make_hit()
    assert h1 == h2


def test_retrieval_hit_project_ids_tuple():
    hit = _make_hit(project_ids=("p1", "p2"))
    assert isinstance(hit.project_ids, tuple)
    assert hit.project_ids == ("p1", "p2")


def test_retrieval_hit_person_ids_tuple():
    hit = _make_hit(person_ids=("u1",))
    assert isinstance(hit.person_ids, tuple)


def test_retrieval_hit_sensitivity_tuple():
    hit = _make_hit(sensitivity=("none",))
    assert isinstance(hit.sensitivity, tuple)


def test_retrieval_hit_fts_only_has_no_vector_score():
    hit = _make_hit(source="fts", vector_score=None, fts_score=0.42, rerank_score=0.42)
    assert hit.vector_score is None
    assert hit.fts_score == 0.42
    assert hit.source == "fts"


def test_retrieval_hit_hybrid_source():
    hit = _make_hit(
        source="hybrid",
        vector_score=0.7,
        fts_score=0.3,
        rerank_score=0.58,
    )
    assert hit.source == "hybrid"


def test_retrieval_hit_snippet_field():
    hit = _make_hit(snippet="short snippet")
    assert hit.snippet == "short snippet"


# ── InsufficientEvidence ─────────────────────────────────────────────────────

def test_insufficient_evidence_default_reason():
    ie = InsufficientEvidence()
    assert "evidence" in ie.reason.lower()


def test_insufficient_evidence_custom_reason():
    ie = InsufficientEvidence(reason="all hits below threshold")
    assert ie.reason == "all hits below threshold"


def test_insufficient_evidence_is_frozen():
    ie = InsufficientEvidence()
    with pytest.raises((AttributeError, TypeError)):
        ie.reason = "other"  # type: ignore[misc]


def test_insufficient_evidence_is_hashable():
    ie = InsufficientEvidence()
    s = {ie}
    assert ie in s


def test_insufficient_evidence_isinstance_check():
    # Callers do isinstance checks, not exception catches.
    result = InsufficientEvidence()
    assert isinstance(result, InsufficientEvidence)
    assert not isinstance(result, RetrievalHit)
