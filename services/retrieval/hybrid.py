"""Hybrid merge and deterministic rerank for L2 retrieval (S7.8).

Public surface:
    hybrid_search(session, mailbox_id, query_embedding, query_text, params,
                  *, reranker=None, now=None)
        -> list[RetrievalHit] | InsufficientEvidence

Algorithm (spec S7.8):
  1. Run vector_search and fts_search independently.
  2. Apply per-hit quality gates on raw scores (min_vector_score for vector
     hits, min_fts_score for FTS-only hits).
  3. Deduplicate by message_id; hits in both pools become source='hybrid'.
  4. Normalize vector scores and FTS scores each to [0, 1] within their pool.
  5. For each candidate compute:
       relevance = v_norm * vector_weight + f_norm * fts_weight
       boost     = project_boost (if project_ids) + person_boost (if person_ids)
       recency   = recency_weight * exp(-age_days / half_life_days)
       score     = relevance + boost + recency
  6. Sort descending by score; stable tie-break by message_id.
  7. Take top rerank_top_k.
  8. Optional Voyage reranker (gated by params.enable_reranking AND
     ENABLE_RERANKING env var).
  9. Return list[RetrievalHit] or InsufficientEvidence if nothing survives.

All weights and boost values come from RetrievalParams fields — nothing is
hardcoded in this module.  The 'now' parameter is injectable for deterministic
tests (avoids wall-clock dependency in recency computations).
"""
from __future__ import annotations

import math
import os
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from services.retrieval.contracts import InsufficientEvidence, RetrievalHit
from services.retrieval.fts import fts_search
from services.retrieval.params import RetrievalParams
from services.retrieval.reranker import NoOpReranker, Reranker
from services.retrieval.vector import vector_search


# ── Internal helpers ──────────────────────────────────────────────────────────

def _normalize(values: list[float]) -> list[float]:
    """Min-max normalize a list of floats to [0, 1].

    Returns [1.0, ...] when all values are identical (avoids 0/0 and ensures
    the single-item case gives a non-zero score).
    """
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return [1.0] * len(values)
    span = hi - lo
    return [(v - lo) / span for v in values]


def _recency(ts: datetime, now: datetime, params: RetrievalParams) -> float:
    """Exponential recency score: recency_weight * exp(-age_days / half_life)."""
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return params.recency_weight * math.exp(-age_days / params.recency_half_life_days)


def _boost(hit: RetrievalHit, params: RetrievalParams) -> float:
    """Additive boost for hits linked to known projects or persons."""
    b = 0.0
    if hit.project_ids:
        b += params.project_boost
    if hit.person_ids:
        b += params.person_boost
    return b


def _merge(
    vec_hits: list[RetrievalHit],
    fts_hits: list[RetrievalHit],
) -> dict[str, RetrievalHit]:
    """Deduplicate by message_id; hits in both pools become source='hybrid'."""
    merged: dict[str, RetrievalHit] = {}

    for hit in vec_hits:
        merged[hit.message_id] = hit  # source='vector'

    for hit in fts_hits:
        if hit.message_id in merged:
            # Appears in both pools: attach FTS score and mark as hybrid.
            merged[hit.message_id] = replace(
                merged[hit.message_id],
                fts_score=hit.fts_score,
                source="hybrid",
            )
        else:
            merged[hit.message_id] = hit  # source='fts'

    return merged


# ── Public surface ────────────────────────────────────────────────────────────

def hybrid_search(
    session: Session,
    mailbox_id: str | UUID,
    query_embedding: list[float],
    query_text: str,
    params: RetrievalParams,
    *,
    reranker: Reranker | None = None,
    now: datetime | None = None,
) -> list[RetrievalHit] | InsufficientEvidence:
    """Run vector + FTS search, merge, score, and return ranked evidence.

    Returns InsufficientEvidence when no hits survive the quality gates or
    when both search functions return empty results.

    ``now`` is injectable for deterministic recency computations in tests;
    defaults to datetime.now(UTC) when None.
    """
    _now = now or datetime.now(timezone.utc)

    # ── 1. Run both searches ──────────────────────────────────────────────────
    vec_hits: list[RetrievalHit] = vector_search(
        session, mailbox_id, query_embedding, params
    )
    fts_hits: list[RetrievalHit] = fts_search(
        session, mailbox_id, query_text, params
    )

    # ── 2. Quality gate on raw scores ─────────────────────────────────────────
    # Vector hits: must meet min_vector_score (default 0.60).
    vec_hits = [h for h in vec_hits
                if (h.vector_score or 0.0) >= params.min_vector_score]

    # FTS hits: must meet min_fts_score (default 0.0 — gate exists for eval tuning).
    fts_hits = [h for h in fts_hits
                if (h.fts_score or 0.0) >= params.min_fts_score]

    if not vec_hits and not fts_hits:
        return InsufficientEvidence("no hits above quality threshold")

    # ── 3. Deduplicate by message_id ──────────────────────────────────────────
    merged = _merge(vec_hits, fts_hits)

    # ── 4. Normalize scores within each pool ─────────────────────────────────
    vec_ids  = [mid for mid, h in merged.items() if h.vector_score is not None]
    fts_ids  = [mid for mid, h in merged.items() if h.fts_score  is not None]

    vec_norm = dict(zip(
        vec_ids,
        _normalize([merged[mid].vector_score for mid in vec_ids]),  # type: ignore[arg-type]
    ))
    fts_norm = dict(zip(
        fts_ids,
        _normalize([merged[mid].fts_score for mid in fts_ids]),  # type: ignore[arg-type]
    ))

    # ── 5. Compute combined score and create new frozen hits ──────────────────
    scored: list[RetrievalHit] = []
    for mid, hit in merged.items():
        v = vec_norm.get(mid, 0.0)
        f = fts_norm.get(mid, 0.0)
        relevance = v * params.vector_weight + f * params.fts_weight
        combined  = relevance + _boost(hit, params) + _recency(hit.ts, _now, params)
        scored.append(replace(hit, rerank_score=combined))

    # ── 6. Sort descending; message_id is the stable tie-breaker ─────────────
    scored.sort(key=lambda h: (-h.rerank_score, h.message_id))

    # ── 7. Take top rerank_top_k ──────────────────────────────────────────────
    top_hits = scored[:params.rerank_top_k]

    # ── 8. Optional reranker (gated by flag AND env var) ─────────────────────
    active_reranker: Reranker = (
        reranker
        if reranker is not None
           and params.enable_reranking
           and os.environ.get("ENABLE_RERANKING")
        else NoOpReranker()
    )
    result = active_reranker.rerank(query_text, top_hits)

    # ── 9. Final check after reranker ─────────────────────────────────────────
    if not result:
        return InsufficientEvidence("reranker returned empty list")

    return result
