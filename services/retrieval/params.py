"""RetrievalParams — runtime defaults for L2 hybrid retrieval (S7.4, D12).

All fields are dataclass defaults so they can be overridden per-request or
per-test without subclassing. FakeEmbedClient tests override embed_model and
embed_dim to small values; production uses the defaults below.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievalParams:
    # ── embedding ─────────────────────────────────────────────────────────────
    embed_model:            str   = "voyage-4"
    embed_dim:              int   = 1024

    # ── retrieval pool sizes ──────────────────────────────────────────────────
    vector_top_k:           int   = 20   # candidates fetched from HNSW index
    fts_top_k:              int   = 20   # candidates fetched from FTS
    rerank_top_k:           int   = 10   # final results returned after rerank

    # ── quality gates ─────────────────────────────────────────────────────────
    # vector: discard hits below this cosine similarity threshold.
    # fts: raise in eval if FTS results are noisy; 0.0 accepts any match.
    min_vector_score:       float = 0.60
    min_fts_score:          float = 0.0

    # ── hybrid scoring weights ────────────────────────────────────────────────
    # Scores are normalised to [0,1] within each pool before combining.
    # recency_weight is kept small so recency cannot dominate relevance.
    vector_weight:          float = 0.6
    fts_weight:             float = 0.4
    recency_weight:         float = 0.05

    # ── filters (default: exclude noise and sensitive content) ────────────────
    include_noise:          bool  = False
    include_sensitive:      bool  = False

    # ── reranking (optional, feature-flagged — see D12c) ─────────────────────
    # Also gated by ENABLE_RERANKING env var at runtime.
    enable_reranking:       bool  = False

    # ── entity boosting ───────────────────────────────────────────────────────
    project_boost:          float = 0.15
    person_boost:           float = 0.10

    # ── recency decay ─────────────────────────────────────────────────────────
    recency_half_life_days: int   = 180
