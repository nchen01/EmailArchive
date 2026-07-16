"""Handoff package DTOs — creator draft/scope/generate slice (S17.3).

Product/API-layer only (not ekc_schemas). Publish/recipient/session DTOs are
deferred to the publish/recipient sprint.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CreateHandoffRequest(BaseModel):
    reason: str = Field(..., description="vacation|leave|transfer|delegation|other")
    title: str = ""


class ScopeRequest(BaseModel):
    # Raw YYYY-MM-DD strings; validated with the same parser as S16.0 ingest.
    date_from: str | None = None
    date_to: str | None = None
    included_project_ids: list[str] = Field(default_factory=list)
    included_person_ids: list[str] = Field(default_factory=list)
    included_thread_ids: list[str] = Field(default_factory=list)
    excluded_thread_ids: list[str] = Field(default_factory=list)
    excluded_message_id_headers: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    keyword_filters: list[str] = Field(default_factory=list)


class HandoffScopeOut(BaseModel):
    date_from: str | None
    date_to: str | None
    included_project_ids: list[str]
    included_person_ids: list[str]
    included_thread_ids: list[str]
    excluded_thread_ids: list[str]
    excluded_message_id_headers: list[str]
    allowed_domains: list[str]
    keyword_filters: list[str]


class HandoffClaimOut(BaseModel):
    id: str
    kind: str
    text: str
    project_id: str | None
    source_message_id_headers: list[str]
    confidence: float


class HandoffEvidenceOut(BaseModel):
    message_id_header: str
    subject: str
    sender_display: str
    sender_domain: str
    date: str  # ISO 8601 ("" if unknown)
    body_snapshot: str
    source_type: str | None


class HandoffPackageOut(BaseModel):
    id: str
    mailbox_id: str
    creator_email: str
    status: str
    reason: str
    title: str
    version: int
    created_at: str
    updated_at: str
    scope: HandoffScopeOut
    claims: list[HandoffClaimOut]
    evidence: list[HandoffEvidenceOut]
    # Creator-only aggregate exclusion counts (never shown to a recipient).
    exclusion_counts: dict[str, int]
