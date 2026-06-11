"""Reranker protocol and default no-op implementation (S7.12 / S7.8 dependency).

The VoyageReranker (full S7.12 implementation) is a separate task.  This module
provides the Protocol that hybrid.py depends on and the NoOpReranker used when
reranking is disabled (default) or when no reranker is injected.

All reranker implementations must be listed in __all__ and must satisfy the
Reranker protocol so hybrid_search can accept any of them via the reranker=
parameter.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from services.retrieval.contracts import RetrievalHit


@runtime_checkable
class Reranker(Protocol):
    """Any object that can reorder a list of RetrievalHit by a query string."""

    def rerank(self, query: str, candidates: list[RetrievalHit]) -> list[RetrievalHit]:
        """Return candidates reordered by relevance to query.

        The returned list must be a subset (or equal set) of candidates; new
        hits must not be invented.  Implementations update rerank_score on
        each hit they return.  The caller trusts the returned order.
        """
        ...


class NoOpReranker:
    """Identity reranker — returns candidates unchanged.

    Used when ENABLE_RERANKING is not set or params.enable_reranking is False.
    Also the correct default for unit tests that want deterministic scores
    without a live Voyage rerank call.
    """

    def rerank(self, query: str, candidates: list[RetrievalHit]) -> list[RetrievalHit]:
        return list(candidates)


__all__ = ["Reranker", "NoOpReranker"]
