"""Shared data contracts for L2 hybrid retrieval (S7.9).

Exports:
- RetrievalHit        — frozen value object returned by all retrieval functions.
- InsufficientEvidence — typed sentinel returned when no usable evidence found.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class RetrievalHit:
    """Evidence hit returned by vector_search, fts_search, and hybrid_search.

    All IDs are UUID-format strings (matching the rest of the codebase).
    project_ids and person_ids use tuples so the frozen dataclass is hashable.
    snippet is the first 300 characters of clean_text.
    vector_score is None for FTS-only results; fts_score is None for vector-only.
    rerank_score holds the final combined score after hybrid merge; for single-
    source hits it equals whichever source score is present.
    """
    message_id:        str
    message_id_header: str                # RFC 5322 citation key
    thread_id:         str
    project_ids:       tuple[str, ...]    # projects the message's thread belongs to
    person_ids:        tuple[str, ...]    # resolved person IDs from sender/to/cc
    ts:                datetime
    subject:           str
    snippet:           str                # first 300 chars of clean_text
    vector_score:      float | None       # cosine similarity [0,1]; None if FTS-only
    fts_score:         float | None       # ts_rank; None if vector-only
    rerank_score:      float              # final combined score after hybrid merge
    source:            Literal["vector", "fts", "hybrid"]
    sensitivity:       tuple[str, ...]
    noise:             bool


@dataclass(frozen=True)
class InsufficientEvidence:
    """Typed sentinel returned when retrieval produces no usable evidence.

    Not an exception — callers check ``isinstance(result, InsufficientEvidence)``.
    """
    reason: str = "no usable evidence found"
