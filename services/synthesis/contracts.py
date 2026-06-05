"""Synthesis request/response contracts with the citation validator (4.6).

The grounding contract — "no citation, no claim" (implementation-plan §4;
AGENTS §3 #3; D8 spirit) — is enforced here: ``SynthesisClaim`` rejects any
claim with an empty ``source_message_ids`` (the Track-B analogue of the Event
schema's ``min_length=1``). Uncited claims never leave the synthesis layer.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SynthesisClaim(BaseModel):
    """One grounded claim. Must cite >=1 message_id_header (NOT internal ids)."""
    text: str = Field(..., description="One factual clause about the owner's work.")
    source_message_ids: list[str] = Field(
        ...,
        min_length=1,
        description="message_id_header value(s) that evidence this claim. REQUIRED.",
    )


class SynthesisResult(BaseModel):
    """The structured, cited response that leaves the synthesis layer."""
    claims: list[SynthesisClaim] = Field(default_factory=list)
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)
    state: str | None = Field(
        None,
        description="Optional framing, e.g. 'no evidenced activity in email'.",
    )
