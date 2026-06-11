"""Retrieval eval cases for the S7.10 hard-gate eval (spec S7.10).

These cases run against the standard 18-message fixture mailbox embedded with
FakeEmbedClient (offline CI) or VoyageEmbedClient (live L2 validation).
All expected_headers and forbidden_headers are RFC 5322 Message-ID values
taken directly from fixtures/mailbox.json (angle brackets stripped by norm_mid).

expected_route describes the intended routing once S7.11 wires L1+L2 together.

Two RetrievalParams sets are provided:

EVAL_PARAMS (default, offline CI):
    embed_model="fake-embed", min_vector_score=0.30.
    FakeEmbedClient feature-hash embeddings produce cosine similarities in the
    0.4–0.7 range for strong vocabulary matches — lower than voyage-4
    (typically 0.7–0.95) because FakeEmbedClient has no semantic generalisation.
    The 0.30 gate passes all clearly-relevant messages while blocking the
    zero-similarity xyzzy case.

VOYAGE_EVAL_PARAMS (live validation, --embed-client voyage):
    embed_model="voyage-4", min_vector_score=0.60.
    Must match the model used for the backfill — vector_search filters
    message_embedding WHERE embed_model = :model, so a mismatch returns zero
    vector hits.  Use this set when querying voyage-4-indexed document vectors
    with a VoyageEmbedClient so query and document embeddings are in the same
    space.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from services.retrieval.params import RetrievalParams

# ── Eval-specific retrieval params ────────────────────────────────────────────

EVAL_PARAMS = RetrievalParams(
    embed_model="fake-embed",
    embed_dim=1024,
    min_vector_score=0.30,   # calibrated for FakeEmbedClient; see module docstring
    min_fts_score=0.0,
    vector_top_k=20,
    fts_top_k=20,
    rerank_top_k=10,
)

# Use this when running the eval with --embed-client voyage (live L2 validation).
# embed_model must match the model used for the backfill so vector_search finds rows.
VOYAGE_EVAL_PARAMS = RetrievalParams(
    embed_model="voyage-4",
    embed_dim=1024,
    min_vector_score=0.60,   # production gate; voyage-4 scores typically 0.7–0.95
    min_fts_score=0.0,
    vector_top_k=20,
    fts_top_k=20,
    rerank_top_k=10,
)


# ── Fixture shape ─────────────────────────────────────────────────────────────

@dataclass
class RetrievalCase:
    """One eval query with expected and forbidden message_id_header values.

    expected_headers: every one of these must appear in the top-10 results.
    forbidden_headers: none of these may appear at any rank.
    expected_route: the intended routing decision once S7.11 is wired.
    allow_sensitive_in_result: if False (default), any sensitive hit is a failure.
    """
    query:                    str
    expected_headers:         list[str]
    forbidden_headers:        list[str]
    expected_route:           Literal["l1_exact", "l2_fallback", "hybrid"]
    allow_sensitive_in_result: bool = False


# ── Eval cases ─────────────────────────────────────────────────────────────────
#
# Header format note: the ingest pipeline strips RFC 5322 angle brackets when
# storing message_id_header, so "atlas-1@acme.com" is the correct form — NOT
# "<atlas-1@acme.com>".
#
# Fixture message inventory (non-noise, non-sensitive — these are embeddable):
#   atlas-1@acme.com          Atlas Migration: cutover plan
#   atlas-2@acme.com          Re: Atlas Migration: cutover plan
#   atlas-3@acme.com          Re: Atlas Migration: cutover plan
#   atlas-sow-1@datapipe.com  DataPipe migration SOW redlines
#   atlas-sow-2@acme.com      Re: DataPipe migration SOW redlines
#   atlas-signoff-1@acme.com  FWD: final cutover sign-off
#   bor-1@acme.com            Borealis Launch kickoff
#   bor-2@cloudpeak.io        Re: Borealis Launch kickoff
#   synthetic:0f341eff...     Borealis launch copy review
#   eval-1@vertexlabs.com     Interested in Borealis — eval access?
#   sync-1@acme.com           Weekly sync notes
#   ren-1@northwind.com       Q3 Renewals — Northwind
#   ren-2@acme.com            Re: Q3 Renewals — Northwind
#   qq-dana@northwind.com     Re: quick question
#   qq-marcus@cloudpeak.io    Re: quick question
#
# Excluded from embedding (backfill skips them):
#   news-1@updates.examplesaas.com  noise=True
#   hr-1@acme.com                   sensitivity=['hr']
#   legal-1@morrislaw.com           sensitivity=['privileged','legal']

EVAL_CASES: list[RetrievalCase] = [

    # ── C1: Atlas Migration thread ─────────────────────────────────────────────
    # FakeEmbedClient scores (dim=1024, query="atlas migration cutover plan"):
    #   atlas-2  0.722  atlas-1  0.674  atlas-3  0.632  — all above 0.30 gate.
    RetrievalCase(
        query="atlas migration cutover plan",
        expected_headers=[
            "atlas-1@acme.com",
            "atlas-2@acme.com",
            "atlas-3@acme.com",
        ],
        forbidden_headers=[
            "news-1@updates.examplesaas.com",   # noise
            "hr-1@acme.com",                    # sensitive
            "legal-1@morrislaw.com",             # sensitive
        ],
        expected_route="hybrid",
    ),

    # ── C2: DataPipe SOW ───────────────────────────────────────────────────────
    # FakeEmbedClient scores: sow-2 0.516, sow-1 0.482 — both above 0.30 gate.
    # legal-1 mentions "DataPipe" but sensitivity=['privileged','legal'] → excluded.
    # "contract" was removed from the query: websearch_to_tsquery requires ALL
    # terms (AND semantics), and "contract" does not appear in the SOW message
    # subjects or bodies, causing FTS to return zero hits for that case.
    RetrievalCase(
        query="DataPipe migration SOW redlines",
        expected_headers=[
            "atlas-sow-1@datapipe.com",
            "atlas-sow-2@acme.com",
        ],
        forbidden_headers=[
            "news-1@updates.examplesaas.com",
            "legal-1@morrislaw.com",
        ],
        expected_route="l2_fallback",
    ),

    # ── C3: Borealis launch ────────────────────────────────────────────────────
    # FakeEmbedClient scores: bor-1 0.722, bor-2 0.522 — both above 0.30 gate.
    RetrievalCase(
        query="Borealis launch kickoff",
        expected_headers=[
            "bor-1@acme.com",
            "bor-2@cloudpeak.io",
        ],
        forbidden_headers=[
            "news-1@updates.examplesaas.com",
        ],
        expected_route="hybrid",
    ),

    # ── C4: Northwind renewals ─────────────────────────────────────────────────
    # ren-1 vector 0.348 (above 0.30 gate); ren-2 vector 0.258 (below gate).
    # Query excludes "account" (that word is absent from both messages' bodies,
    # making the FTS tsquery require a term that's never matched).
    # "northwind renewals Q3" FTS matches both via subject_clean_tsv, so ren-2
    # is recovered through the FTS pool even though its vector score is low.
    RetrievalCase(
        query="Northwind renewals Q3",
        expected_headers=[
            "ren-1@northwind.com",
            "ren-2@acme.com",
        ],
        forbidden_headers=[
            "news-1@updates.examplesaas.com",
        ],
        expected_route="l2_fallback",
    ),

    # ── C5: Sensitive gate — HR message must never appear ──────────────────────
    # hr-1 has sensitivity=['hr']; it is not embedded and is SQL-filtered.
    # No expected_headers: the gate is only that the forbidden message is absent.
    RetrievalCase(
        query="compensation salary review 2026",
        expected_headers=[],
        forbidden_headers=[
            "hr-1@acme.com",
        ],
        expected_route="l2_fallback",
    ),

    # ── C6: Sensitive gate — legal/privileged message must never appear ─────────
    RetrievalCase(
        query="attorney privileged DataPipe legal contract",
        expected_headers=[],
        forbidden_headers=[
            "legal-1@morrislaw.com",
        ],
        expected_route="l2_fallback",
    ),

    # ── C7: Unanswerable query → InsufficientEvidence ─────────────────────────
    # No fixture message contains these tokens; FakeEmbedClient scores all = 0.0.
    # websearch_to_tsquery finds no FTS matches either.
    # With min_vector_score=0.30 and no FTS hits, hybrid_search returns
    # InsufficientEvidence.
    RetrievalCase(
        query="xyzzy frobnicator spaghetti",
        expected_headers=[],
        forbidden_headers=[],
        expected_route="l2_fallback",
    ),
]
