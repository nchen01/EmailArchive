"""Cover-for-me request/response DTOs (S5, D11, implementation-plan §6.3).

The natural-language query surface answering the §3 job — "Who do I ask about Y
/ what's the state of project Z?". Bounded to structured L1 objects; the cited
answer is the reused S4 ``SynthesisResult``.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from services.synthesis.contracts import SynthesisResult


class CoverForMeRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


class CoverForMeResponse(BaseModel):
    query: str  # echo back
    routed_to: str | None  # "person:<name>", "project:<label>", or None
    result: SynthesisResult
