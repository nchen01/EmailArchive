"""Cover-for-me request/response DTOs (S5/S8, D11, implementation-plan §6.3).

S8.2/S8.4 additions to CoverForMeResponse:
  - supporting_evidence: metadata for every message cited in result.claims.
    Derived from cited source_message_ids only — not from all retrieval hits.
  - retrieval_status: structured operational state for L2 retrieval, letting
    the UI render distinct indicators for missing keys, rate limits, etc.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from services.synthesis.contracts import SynthesisResult

# Structured operational states for L2 retrieval.  Rendered as distinct
# indicators in the UI so users and operators can diagnose configuration issues.
RetrievalStatus = Literal[
    "active",              # L2 ran and returned hits that were used in synthesis
    "active_l1_only",      # L2 ran but returned no hits above threshold; L1 answered
    "disabled_no_key",     # VOYAGE_API_KEY absent — L1-only, expected in dev
    "degraded_rate_limit", # Voyage rate-limit hit — L2 skipped for this request
    "no_embeddings",       # No message_embedding rows for this mailbox + model
    "unavailable",         # Voyage client or request failed for another reason
]


class EvidenceMessage(BaseModel):
    """Metadata for one cited source message, included in the response so
    the UI can show subject and date instead of a raw message_id_header."""
    message_id_header: str
    subject: str
    date: str    # ISO 8601 timestamp string
    snippet: str  # first 200 chars of clean_text


class CoverForMeRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


class CoverForMeResponse(BaseModel):
    query: str           # echoed back
    routed_to: str | None  # "person:<name>", "project:<label>", or None
    result: SynthesisResult
    # S8.2: one EvidenceMessage per unique header cited across result.claims.
    # Never contains uncited retrieval hits.
    supporting_evidence: list[EvidenceMessage] = Field(default_factory=list)
    # S8.4: structured operational state; defaults to "active" for old clients.
    retrieval_status: RetrievalStatus = "active"
