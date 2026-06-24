"""Pydantic contracts for the relationship map (S13).

These shapes are the API surface for ``GET /api/relationship-map/{mailbox_id}``.
They are intentionally evidence-safe: citations are ``message_id_header`` strings,
structural evidence is thread/project ids, and there is no field that carries raw
body text or sensitive content.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NodeType = Literal["owner", "person", "project", "organization", "thread_group"]

RelationshipType = Literal[
    "direct_exchange",     # owner <-> person communication (from the Edge table)
    "thread_copresence",   # people appear together in eligible project threads
    "project_copresence",  # people share a materialized project
    "org_affiliation",     # person belongs to an org/domain
    "bridge",              # person spans multiple projects
]

EvidenceKind = Literal["message_headers", "thread_ids", "project_ids", "domain"]

MapMode = Literal["owner", "project", "org", "graph"]
LayoutHint = Literal["tree", "graph"]


class RelationshipNode(BaseModel):
    id: str
    node_type: NodeType
    label: str
    subtitle: str | None = None
    role: str | None = None
    confidence: float | None = None
    # Safe, non-sensitive structured extras (e.g. is_bridge, project_count,
    # org_domain, internal). Never contains body text.
    metadata: dict = Field(default_factory=dict)


class RelationshipEdge(BaseModel):
    id: str
    source_id: str
    target_id: str
    relationship_type: RelationshipType
    evidence_kind: EvidenceKind
    # Volume / evidence strength — NOT importance. The UI must label it as such.
    weight: float
    confidence: float | None = None
    # How many independent pieces of evidence back this edge (threads, projects,
    # or message headers). Drives the "Appeared together in N threads" copy.
    evidence_count: int = 0
    source_message_ids: list[str] = Field(default_factory=list)
    thread_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    muted: bool = False
    explanation: str = ""


class RelationshipGroup(BaseModel):
    """A logical grouping of nodes (e.g. an org bucket in org mode)."""
    id: str
    label: str
    node_ids: list[str] = Field(default_factory=list)


class GeneratedFrom(BaseModel):
    threads: int = 0
    projects: int = 0
    messages: int = 0
    eligible_threads: int = 0
    excluded_threads: int = 0


class RelationshipMapResponse(BaseModel):
    root: RelationshipNode | None
    nodes: list[RelationshipNode]
    edges: list[RelationshipEdge]
    groups: list[RelationshipGroup] = Field(default_factory=list)
    layout_hint: LayoutHint
    mode: MapMode
    generated_from: GeneratedFrom
