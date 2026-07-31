"""Handoff package DTOs — creator draft/scope/generate slice (S17.3) plus the
publish + recipient-view foundation (S17.5).

Product/API-layer only (not ekc_schemas).
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


class GenerationDiagnostic(BaseModel):
    """Creator-only explanation for an EMPTY generated candidate (S17.13). Present
    only when a generated package has no claims and no evidence, so the creator UI
    can say *why* (esp. `no_events_for_mailbox`, where widening the date range will
    not help). Never returned to a recipient — no existence oracle."""
    code: str  # no_events_for_mailbox | no_events_in_scope | all_events_excluded_by_policy
    event_count: int


class HandoffPackageOut(BaseModel):
    id: str
    mailbox_id: str
    creator_email: str
    status: str
    reason: str
    title: str
    package_type: str = "coverage"  # 'coverage' | 'return_delta' (S34)
    version: int
    created_at: str
    updated_at: str
    published_at: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None
    scope: HandoffScopeOut
    claims: list[HandoffClaimOut]
    evidence: list[HandoffEvidenceOut]
    # Creator-only aggregate exclusion counts (never shown to a recipient).
    exclusion_counts: dict[str, int]
    # Creator-only: why an empty generated candidate is empty (S17.13); else null.
    generation: GenerationDiagnostic | None = None


# ── Publish (creator) — S17.5 ────────────────────────────────────────────────

class PublishRequest(BaseModel):
    recipient_email: str = Field(..., description="the single coverage recipient")
    # Override the default 30-day validity (published_at + expires_in_days).
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class PublishResponse(BaseModel):
    """Returned ONCE to the creator on publish. `capability_code` is the only time
    the raw code is exposed; the server stores only its hash. Place it in the
    share-link URL *fragment* (`share_fragment`), never a path/query."""
    package: HandoffPackageOut
    recipient_email: str
    expires_at: str
    capability_code: str
    share_fragment: str


# ── Recipient view — S17.5 ───────────────────────────────────────────────────

class RecipientSessionRequest(BaseModel):
    # The one-time capability code, read by the SPA from the share-link fragment
    # and sent in the POST body (never a URL path/query).
    code: str = Field(..., min_length=1)


class RecipientSessionResponse(BaseModel):
    session_token: str  # short-lived bearer; send as Authorization: Bearer <token>
    expires_at: str     # session expiry (ISO 8601)
    package_id: str


class RecipientClaimOut(BaseModel):
    id: str
    kind: str
    text: str
    project_id: str | None
    source_message_id_headers: list[str]
    confidence: float


class RecipientEvidenceOut(BaseModel):
    """Package-local evidence. Deliberately has NO Gmail/open_url or source-mailbox
    link — the recipient reads snapshotted content only."""
    message_id_header: str
    subject: str
    sender_display: str
    sender_domain: str
    date: str
    body_snapshot: str
    source_type: str | None


class PrivacyPosture(BaseModel):
    """Global, package-invariant posture. Carries NO counts and NO per-topic
    signal — a constant statement, so it can never act as an existence oracle for
    whether sensitive/excluded content existed in THIS package."""
    scope_limited: bool = True
    sensitive_excluded: bool = True
    note: str = (
        "This package contains only the messages the sender chose to include. "
        "Sensitive and out-of-scope content has been excluded, and the underlying "
        "mailbox is not accessible from here."
    )


class RecipientPackageOut(BaseModel):
    """Post-publish recipient view: package-local, read-only, snapshotted. No
    exclusion counts, no Gmail/source link, no live mailbox — see PrivacyPosture."""
    package_id: str
    title: str
    reason: str
    creator_email: str
    package_type: str = "coverage"  # 'coverage' | 'return_delta' — drives recipient framing (S34)
    published_at: str | None
    expires_at: str | None
    claims: list[RecipientClaimOut]
    evidence: list[RecipientEvidenceOut]
    privacy_posture: PrivacyPosture


# ── Recipient package-local ask — S17.9 ──────────────────────────────────────

class RecipientAskRequest(BaseModel):
    # The recipient's question, asked against ONLY this package's snapshot. Sent
    # in the POST body; the recipient session travels in the Authorization header.
    query: str = Field(..., min_length=1, max_length=1000)


class RecipientAnswerClaimOut(BaseModel):
    """A package claim surfaced as part of an answer. Every header in
    `source_message_id_headers` is an in-package HandoffEvidence header."""
    id: str
    kind: str
    text: str
    source_message_id_headers: list[str]


class RecipientAskResponse(BaseModel):
    """Deterministic, package-local answer. `answered` is False for no match /
    sensitive / unknown / insufficient evidence — ALL identical, so the response
    never reveals whether excluded content exists. Citations are package evidence
    rows only (no Gmail/source link, no mailbox id, no exclusion counts)."""
    answered: bool
    message: str
    claims: list[RecipientAnswerClaimOut]
    evidence: list[RecipientEvidenceOut]
