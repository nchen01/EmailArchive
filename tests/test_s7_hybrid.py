"""S7.8 — hybrid merge, scoring, quality gate, and reranker tests.

All tests here are offline (no DB). hybrid_search is tested by injecting
pre-built RetrievalHit lists via mock vector_search / fts_search so the merge,
normalization, scoring, and gate logic can be validated without a real DB.

Acceptance criteria verified:
- Deterministic: same inputs always produce same ranked list.
- Recency cannot push a low-relevance message above a high-relevance one.
- Boost and recency computations have isolated unit tests.
- Reranker path tested with a mock; never calls the API.
- Quality gate: InsufficientEvidence when all hits below threshold.
- Deduplication: same message_id in both pools → source='hybrid', both scores.
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.retrieval.contracts import InsufficientEvidence, RetrievalHit
from services.retrieval.hybrid import _boost, _merge, _normalize, _recency
from services.retrieval.params import RetrievalParams
from services.retrieval.reranker import NoOpReranker, Reranker

_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _hit(
    msg_id: str,
    *,
    vector_score: float | None = None,
    fts_score: float | None = None,
    source: str = "vector",
    ts: datetime | None = None,
    project_ids: tuple = (),
    person_ids: tuple = (),
) -> RetrievalHit:
    score = vector_score if vector_score is not None else (fts_score or 0.0)
    return RetrievalHit(
        message_id=msg_id,
        message_id_header=f"<{msg_id}@x>",
        thread_id=f"t-{msg_id}",
        project_ids=project_ids,
        person_ids=person_ids,
        ts=ts or _NOW,
        subject=f"Subject {msg_id}",
        snippet=f"Snippet for {msg_id}",
        vector_score=vector_score,
        fts_score=fts_score,
        rerank_score=score,
        source=source,  # type: ignore[arg-type]
        sensitivity=("none",),
        noise=False,
    )


def _run_hybrid(
    vec_hits: list[RetrievalHit],
    fts_hits: list[RetrievalHit],
    params: RetrievalParams | None = None,
    *,
    now: datetime = _NOW,
    reranker=None,
):
    """Call hybrid_search with mock search functions injected via patch."""
    from services.retrieval.hybrid import hybrid_search

    _params = params or RetrievalParams(
        embed_model="fake-embed",
        embed_dim=1024,
        min_vector_score=0.0,   # disable gate so all hits survive in most tests
        min_fts_score=0.0,
    )

    with patch("services.retrieval.hybrid.vector_search", return_value=vec_hits), \
         patch("services.retrieval.hybrid.fts_search",    return_value=fts_hits):
        return hybrid_search(
            session=MagicMock(),
            mailbox_id="test-mailbox",
            query_embedding=[0.0] * 1024,
            query_text="test query",
            params=_params,
            reranker=reranker,
            now=now,
        )


# ── _normalize ────────────────────────────────────────────────────────────────

def test_normalize_empty():
    assert _normalize([]) == []


def test_normalize_single():
    assert _normalize([0.7]) == [1.0]


def test_normalize_all_equal():
    assert _normalize([0.5, 0.5, 0.5]) == [1.0, 1.0, 1.0]


def test_normalize_range():
    result = _normalize([0.0, 0.5, 1.0])
    assert abs(result[0] - 0.0) < 1e-9
    assert abs(result[1] - 0.5) < 1e-9
    assert abs(result[2] - 1.0) < 1e-9


def test_normalize_preserves_order():
    values = [0.3, 0.9, 0.1, 0.7]
    normed = _normalize(values)
    pairs = sorted(zip(values, normed), key=lambda p: p[0])
    assert pairs == sorted(pairs, key=lambda p: p[1])


# ── _recency ──────────────────────────────────────────────────────────────────

def test_recency_recent_message_higher_than_old():
    params = RetrievalParams()
    recent_ts = _NOW - timedelta(days=1)
    old_ts    = _NOW - timedelta(days=365)
    assert _recency(recent_ts, _NOW, params) > _recency(old_ts, _NOW, params)


def test_recency_respects_half_life():
    params = RetrievalParams(recency_weight=1.0, recency_half_life_days=180)
    ts_half = _NOW - timedelta(days=180)
    score   = _recency(ts_half, _NOW, params)
    # After one half-life, score should be recency_weight * exp(-1) ≈ 0.368
    assert abs(score - math.exp(-1.0)) < 1e-6


def test_recency_cannot_dominate_relevance():
    """Recency score at t=0 must be less than the full relevance contribution
    of a vector_weight=0.6 hit, confirming the 'small' default."""
    params = RetrievalParams()
    max_recency = params.recency_weight * 1.0   # exp(0) = 1, age=0
    full_vector = params.vector_weight * 1.0    # normalized score = 1
    assert max_recency < full_vector


def test_recency_future_timestamp_clamped_to_zero():
    """Timestamps in the future should not produce negative age → 0 decay."""
    params  = RetrievalParams()
    future  = _NOW + timedelta(days=10)
    score   = _recency(future, _NOW, params)
    # age_days clamped to 0 → exp(0) = 1 → recency_weight * 1
    assert abs(score - params.recency_weight) < 1e-9


# ── _boost ─────────────────────────────────────────────────────────────────────

def test_boost_no_associations():
    params = RetrievalParams()
    hit = _hit("m1")
    assert _boost(hit, params) == 0.0


def test_boost_project_only():
    params = RetrievalParams()
    hit = _hit("m1", project_ids=("p1",))
    assert abs(_boost(hit, params) - params.project_boost) < 1e-9


def test_boost_person_only():
    params = RetrievalParams()
    hit = _hit("m1", person_ids=("u1",))
    assert abs(_boost(hit, params) - params.person_boost) < 1e-9


def test_boost_both_additive():
    params = RetrievalParams()
    hit = _hit("m1", project_ids=("p1",), person_ids=("u1",))
    expected = params.project_boost + params.person_boost
    assert abs(_boost(hit, params) - expected) < 1e-9


def test_boost_multiple_projects_counts_once():
    """Boost is binary (has/lacks associations), not per-entity count."""
    params = RetrievalParams()
    hit_one   = _hit("m1", project_ids=("p1",))
    hit_three = _hit("m2", project_ids=("p1", "p2", "p3"))
    assert _boost(hit_one, params) == _boost(hit_three, params)


# ── _merge ────────────────────────────────────────────────────────────────────

def test_merge_vector_only():
    h = _hit("m1", vector_score=0.9)
    result = _merge([h], [])
    assert result["m1"].source == "vector"
    assert result["m1"].fts_score is None


def test_merge_fts_only():
    h = _hit("m1", fts_score=0.4, source="fts")
    result = _merge([], [h])
    assert result["m1"].source == "fts"
    assert result["m1"].vector_score is None


def test_merge_hybrid_combines_scores():
    vec = _hit("m1", vector_score=0.9)
    fts = _hit("m1", fts_score=0.3, source="fts")
    result = _merge([vec], [fts])
    assert result["m1"].source == "hybrid"
    assert result["m1"].vector_score == 0.9
    assert result["m1"].fts_score    == 0.3


def test_merge_no_duplicates():
    vec = [_hit(f"v{i}", vector_score=0.9) for i in range(3)]
    fts = [_hit(f"f{i}", fts_score=0.3, source="fts") for i in range(3)]
    result = _merge(vec, fts)
    assert len(result) == 6


def test_merge_deduplicates_correctly():
    """Three messages: one vector-only, one fts-only, one in both."""
    vec = [_hit("shared", vector_score=0.8), _hit("vec_only", vector_score=0.7)]
    fts = [_hit("shared", fts_score=0.3, source="fts"),
           _hit("fts_only", fts_score=0.2, source="fts")]
    result = _merge(vec, fts)
    assert len(result) == 3
    assert result["shared"].source == "hybrid"
    assert result["vec_only"].source == "vector"
    assert result["fts_only"].source == "fts"


# ── hybrid_search — quality gate ──────────────────────────────────────────────

def test_no_hits_returns_insufficient_evidence():
    result = _run_hybrid([], [])
    assert isinstance(result, InsufficientEvidence)


def test_all_below_vector_threshold_returns_insufficient_evidence():
    params = RetrievalParams(min_vector_score=0.70, min_fts_score=0.0,
                             embed_model="fake-embed", embed_dim=1024)
    vec = [_hit("m1", vector_score=0.50), _hit("m2", vector_score=0.60)]
    result = _run_hybrid(vec, [], params=params)
    assert isinstance(result, InsufficientEvidence)


def test_surviving_hits_pass_through():
    vec = [_hit("m1", vector_score=0.90)]
    result = _run_hybrid(vec, [])
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].message_id == "m1"


# ── hybrid_search — scoring and ordering ─────────────────────────────────────

def test_output_sorted_descending_by_rerank_score():
    vec = [
        _hit("m1", vector_score=0.9),
        _hit("m2", vector_score=0.5),
        _hit("m3", vector_score=0.7),
    ]
    result = _run_hybrid(vec, [])
    assert isinstance(result, list)
    scores = [h.rerank_score for h in result]
    assert scores == sorted(scores, reverse=True)


def test_deterministic_same_inputs_same_order():
    vec = [_hit(f"m{i}", vector_score=0.9 - i * 0.1) for i in range(5)]
    r1 = _run_hybrid(list(vec), [])
    r2 = _run_hybrid(list(vec), [])
    assert [h.message_id for h in r1] == [h.message_id for h in r2]


def test_old_high_relevance_beats_recent_low_relevance():
    """Recency must not push a low-relevance message above a high-relevance one."""
    params = RetrievalParams(
        min_vector_score=0.0,
        embed_model="fake-embed",
        embed_dim=1024,
    )
    old_high  = _hit("old",    vector_score=0.95,
                     ts=_NOW - timedelta(days=500))
    new_low   = _hit("recent", vector_score=0.10,
                     ts=_NOW - timedelta(days=1))
    result = _run_hybrid([old_high, new_low], [], params=params, now=_NOW)
    assert isinstance(result, list)
    assert result[0].message_id == "old", (
        "High-relevance old message must rank above low-relevance recent one"
    )


def test_rerank_top_k_respected():
    params = RetrievalParams(
        rerank_top_k=2, min_vector_score=0.0,
        embed_model="fake-embed", embed_dim=1024,
    )
    vec = [_hit(f"m{i}", vector_score=0.9 - i * 0.05) for i in range(5)]
    result = _run_hybrid(vec, [], params=params)
    assert isinstance(result, list)
    assert len(result) == 2


def test_hybrid_hit_uses_both_scores_in_scoring():
    """A message in both pools must have higher rerank_score than vector-only."""
    params = RetrievalParams(
        min_vector_score=0.0, embed_model="fake-embed", embed_dim=1024
    )
    vec_score = 0.70
    fts_score = 0.50

    hybrid_msg   = _hit("shared", vector_score=vec_score)
    fts_support  = _hit("shared", fts_score=fts_score, source="fts")
    vec_only_msg = _hit("vec_only", vector_score=vec_score)

    result = _run_hybrid([hybrid_msg, vec_only_msg], [fts_support], params=params)
    assert isinstance(result, list)
    scores = {h.message_id: h.rerank_score for h in result}
    assert scores["shared"] > scores["vec_only"], (
        "Hybrid hit (vector + FTS) must outscore vector-only hit at same vector score"
    )


# ── NoOpReranker ──────────────────────────────────────────────────────────────

def test_noop_reranker_returns_input_unchanged():
    reranker = NoOpReranker()
    hits = [_hit("m1", vector_score=0.9), _hit("m2", vector_score=0.7)]
    result = reranker.rerank("query", hits)
    assert result == hits


def test_noop_reranker_satisfies_protocol():
    assert isinstance(NoOpReranker(), Reranker)


# ── Reranker path — mock reranker (never calls API) ──────────────────────────

def test_reranker_called_when_enabled(monkeypatch):
    """When enable_reranking=True and ENABLE_RERANKING is set, the reranker fires."""
    monkeypatch.setenv("ENABLE_RERANKING", "1")

    called_with: list = []

    class MockReranker:
        def rerank(self, query: str, candidates: list[RetrievalHit]) -> list[RetrievalHit]:
            called_with.append((query, [h.message_id for h in candidates]))
            return candidates  # return unchanged

    params = RetrievalParams(
        enable_reranking=True, min_vector_score=0.0,
        embed_model="fake-embed", embed_dim=1024,
    )
    vec = [_hit("m1", vector_score=0.9)]
    _run_hybrid(vec, [], params=params, reranker=MockReranker())

    assert called_with, "Reranker.rerank must be called when flag + env var are set"
    query, ids = called_with[0]
    assert "m1" in ids


def test_reranker_not_called_without_env_var(monkeypatch):
    """Without ENABLE_RERANKING env var, reranker is bypassed even if param is True."""
    monkeypatch.delenv("ENABLE_RERANKING", raising=False)

    called = []

    class SentinelReranker:
        def rerank(self, query: str, candidates: list[RetrievalHit]) -> list[RetrievalHit]:
            called.append(True)
            return candidates

    params = RetrievalParams(
        enable_reranking=True, min_vector_score=0.0,
        embed_model="fake-embed", embed_dim=1024,
    )
    vec = [_hit("m1", vector_score=0.9)]
    _run_hybrid(vec, [], params=params, reranker=SentinelReranker())

    assert not called, "Reranker must not be called without ENABLE_RERANKING env var"


def test_reranker_not_called_without_param_flag(monkeypatch):
    """Without params.enable_reranking=True, reranker is bypassed even if env is set."""
    monkeypatch.setenv("ENABLE_RERANKING", "1")

    called = []

    class SentinelReranker:
        def rerank(self, query: str, candidates: list[RetrievalHit]) -> list[RetrievalHit]:
            called.append(True)
            return candidates

    params = RetrievalParams(
        enable_reranking=False, min_vector_score=0.0,
        embed_model="fake-embed", embed_dim=1024,
    )
    vec = [_hit("m1", vector_score=0.9)]
    _run_hybrid(vec, [], params=params, reranker=SentinelReranker())

    assert not called, "Reranker must not be called when params.enable_reranking=False"
