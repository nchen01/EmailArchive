"""
Authoritative data contracts for the Email Knowledge Continuity system.

============================================================================
THIS FILE IS THE SINGLE SOURCE OF TRUTH for every persisted / cross-service
object. Specs 00, 01, 02, and 03 describe *behavior*; object *shapes* are
defined here and nowhere else. Services and agents MUST import these models
rather than re-declaring them. If a spec's inline code disagrees with this
file, this file wins.
============================================================================

CONVENTIONS (read before using)

1. IDENTIFIERS — two distinct referencing schemes, do not mix them:
   - Internal foreign keys use the object's `id` (UUIDv4 string). This covers
     Thread.message_ids, Project.thread_ids, Project.member_ids,
     Edge.person_id, Event.actor_person_id, *Assignment.thread_id/project_id.
   - CITATIONS / PROVENANCE use `Message.message_id_header` (the RFC 5322
     Message-ID). Event.source_message_ids and every user-facing citation use
     these — NOT internal ids — because a citation must survive re-ingest and
     resolve to an openable email. (Resolved contradiction: spec 02's API
     examples used RFC ids while internal links use UUIDs; both are correct,
     for different purposes. This split is now explicit.)

2. CITATION CONTRACT — any object asserting something about the mailbox owner's
   work (currently Event) MUST carry >=1 source_message_id. Enforce at write
   time; reject otherwise. "No citation, no claim."

3. CONFIDENCE — inferred fields carry a confidence in [0.0, 1.0]. The UI hides
   inferred facts below a per-tenant display threshold (default 0.4).

4. OWNER HANDLING — the mailbox owner is a participant of (almost) every thread
   and carries no discriminative signal. Convention:
   - Thread.participants INCLUDES the owner (raw record).
   - Edge has no owner row (owner is implicit; edges are owner<->contact).
   - Clustering features (ThreadFeatures, in the enrich service) EXCLUDE owner.
   Handle owner exclusion exactly once, in L1; do not double-strip.

5. DETERMINISM / IDEMPOTENCY — ingest dedupes by message_id_header; enrichment
   is a pure function of inputs + a content hash. Re-running yields identical
   objects and stable ids.

6. COMPUTE-ONLY TYPES are intentionally NOT here. ThreadFeatures (numpy-bearing,
   spec 03 §5) is an in-process artifact of the enrich service, not a persisted
   cross-service contract, and lives in `services/enrich/clustering/features.py`.
   API DTOs (spec 02) are serializations DERIVED from these models and live in
   `services/api`; they are generated, not duplicated.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = "0.1.0"


# ── Enums ───────────────────────────────────────────────────────────────────

class Role(str, Enum):
    AE = "account_exec"
    LEAD = "lead"
    INTERNAL = "internal"
    MANAGER = "manager"
    VENDOR = "vendor"
    UNKNOWN = "unknown"


class Sensitivity(str, Enum):
    NONE = "none"
    PRIVILEGED = "privileged"
    LEGAL = "legal"
    HR = "hr"
    PERSONAL = "personal"


class EventType(str, Enum):
    PROPOSED = "proposed"   # intent / future tense
    DID = "did"             # action taken
    OUTCOME = "outcome"     # confirmed result, evidenced in text


class LabelSource(str, Enum):
    CTFIDF = "ctfidf"       # class-based TF-IDF keyphrase
    ENTITY = "entity"       # named entity (ORG/PRODUCT/...)
    FALLBACK = "fallback"   # "<top contact> · <month>"
    USER = "user"           # sticky user rename


# ── L0 — ingest / normalization (spec 00) ────────────────────────────────────

class Address(BaseModel):
    """A normalized email address. No person resolution at L0."""
    raw: str = Field(..., description="Original header token, kept for audit.")
    email: str = Field(..., description="Lowercased, plus-addressing stripped.")
    display_names: list[str] = Field(default_factory=list)


class AttachmentRef(BaseModel):
    """Attachment metadata. Content is NOT stored by default — sha256 is the key."""
    sha256: str
    filename: str | None = None
    mimetype: str
    size_bytes: int = Field(..., ge=0)


class Message(BaseModel):
    id: str = Field(..., description="Internal UUIDv4.")
    message_id_header: str = Field(
        ..., description="RFC 5322 Message-ID. PROVENANCE / CITATION KEY. "
                         "Synthesized + flagged if the source lacked one (spec 00 §6).")
    provider_id: str = Field(..., description="Gmail/Graph native id.")
    thread_id: str = Field(..., description="Internal Thread.id this message belongs to.")
    sender: Address
    to: list[Address] = Field(default_factory=list)
    cc: list[Address] = Field(default_factory=list)
    ts: datetime = Field(..., description="UTC.")
    subject: str = ""
    clean_text: str = Field("", description="Normalized body for embedding/search.")
    attachment_refs: list[AttachmentRef] = Field(default_factory=list)
    link_domains: list[str] = Field(default_factory=list, description="Registrable domains (PSL).")
    sensitivity: list[Sensitivity] = Field(default_factory=lambda: [Sensitivity.NONE])
    noise: bool = False
    raw_uri: str | None = Field(None, description="Object-store pointer to archived raw MIME.")


class Thread(BaseModel):
    id: str = Field(..., description="Internal UUIDv4.")
    provider_thread_ids: list[str] = Field(default_factory=list)
    root_message_id_header: str | None = None
    message_ids: list[str] = Field(
        default_factory=list, description="Internal Message.id, time-ordered.")
    subject_norm: str = ""
    participants: list[str] = Field(
        default_factory=list,
        description="Normalized emails. OWNER INCLUDED here; L1 strips for features/edges.")
    t_start: datetime
    t_end: datetime
    lineage_conflict: bool = Field(
        False, description="Header vs provider grouping disagreed; flagged for review (spec 00 §7).")


# ── L1 — enrichment (spec 01) ─────────────────────────────────────────────────

class Org(BaseModel):
    id: str
    name: str
    domains: list[str] = Field(default_factory=list)
    internal: bool = False


class Identity(BaseModel):
    """An address-level record linking an email to a resolved Person (spec 01 §3)."""
    email: str = Field(..., description="Normalized address.")
    display_names: list[str] = Field(default_factory=list)
    person_id: str | None = Field(None, description="Resolved Person.id; None until resolved.")


class Person(BaseModel):
    id: str = Field(..., description="Internal UUIDv4.")
    canonical_email: str
    names: list[str] = Field(default_factory=list)
    org_id: str | None = None
    role: Role = Role.UNKNOWN
    role_confidence: float = Field(0.0, ge=0.0, le=1.0)
    identities: list[str] = Field(
        default_factory=list, description="Identity.email values resolved to this person.")


class Edge(BaseModel):
    """Owner<->contact relationship. Owner is implicit (no owner row)."""
    person_id: str = Field(..., description="The contact's Person.id.")
    message_count: int = Field(..., ge=0)
    sent_to_count: int = Field(..., ge=0, description="Owner -> contact.")
    received_count: int = Field(..., ge=0, description="Contact -> owner.")
    first_contact: datetime
    last_contact: datetime
    weight: float = Field(..., ge=0.0, description="Computed in L1 §4 (volume/recency/reciprocity).")


class Event(BaseModel):
    id: str = Field(..., description="Internal UUIDv4.")
    actor_person_id: str
    type: EventType
    summary: str = Field(..., description="One factual clause, no embellishment.")
    project_id: str | None = None
    source_message_ids: list[str] = Field(
        ..., min_length=1,
        description="message_id_header values (NOT internal ids). REQUIRED — grounding contract.")
    confidence: float = Field(0.0, ge=0.0, le=1.0)


# ── Projects (spec 01 §6 + spec 03 deep dive) ─────────────────────────────────
# Resolved contradiction: spec 01 defined a minimal Project; spec 03 defined a
# richer one (members, label_source, debug). The richer definition is canonical;
# spec 01's minimal version is superseded.

class ProjectMember(BaseModel):
    person_id: str
    involvement: float = Field(
        ..., ge=0.0, description="Sum over member threads of assignment.weight * msgs_in_thread.")
    message_count: int = Field(..., ge=0)


class ThreadProjectAssignment(BaseModel):
    """Soft edge between a thread and a project (overlapping membership, spec 03 §10)."""
    thread_id: str = Field(..., description="Internal Thread.id.")
    project_id: str = Field(..., description="Internal Project.id.")
    weight: float = Field(..., gt=0.0, le=1.0, description="Thread->project affinity.")
    is_primary: bool = Field(..., description="True for the thread's argmax project.")


class Project(BaseModel):
    id: str = Field(..., description="Internal UUIDv4, stable across re-clusters (spec 03 §14).")
    label: str
    label_source: LabelSource
    member_ids: list[str] = Field(default_factory=list, description="Person.id, by involvement desc.")
    members: list[ProjectMember] = Field(default_factory=list)
    thread_ids: list[str] = Field(default_factory=list, description="Internal Thread.id.")
    start: datetime
    end: datetime
    confidence: float = Field(..., ge=0.0, le=1.0)
    debug: dict[str, Any] | None = Field(
        None, description="cohesion / separation / n_threads (spec 03 §13).")


class ClusteringResult(BaseModel):
    """Public output contract of the clustering module (spec 03 §3)."""
    projects: list[Project] = Field(default_factory=list)
    assignments: list[ThreadProjectAssignment] = Field(default_factory=list)
    run_meta: dict[str, Any] = Field(
        default_factory=dict,
        description="seed, params_hash, modularity, n_communities, orphan_ratio.")


__all__ = [
    "SCHEMA_VERSION",
    "Role", "Sensitivity", "EventType", "LabelSource",
    "Address", "AttachmentRef", "Message", "Thread",
    "Org", "Identity", "Person", "Edge", "Event",
    "ProjectMember", "ThreadProjectAssignment", "Project", "ClusteringResult",
]
