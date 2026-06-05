"""Project-view response DTOs (spec 02 §5).

Serializations derived from ekc_schemas (Project, Person, Edge, Thread). Events /
L3 summaries are S4 — the ``activity`` field is omitted here (spec 02 §5 marks it
``@sprint S4``).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectMetrics(BaseModel):
    members: int
    threads: int
    last_activity: datetime


class WhoToAsk(BaseModel):
    person_id: str
    name: str
    role: str
    in_project_count: int
    weight: float


class ProjectMemberOut(BaseModel):
    person_id: str
    name: str
    role: str
    in_project_count: int
    involvement: float


class RecentThreadOut(BaseModel):
    thread_id: str
    subject: str
    participants: list[str]
    last: datetime


class ActivityItemOut(BaseModel):
    """An Event row rendered into the project ``activity`` panel (spec 02 §5/§6).

    Derived from ``ekc_schemas.Event``. Hard invariant: every item ships >=1
    ``source_message_ids`` (the DB CHECK guarantees it; asserted at this
    boundary too).
    """
    type: str  # proposed | did | outcome
    summary: str
    actor_person_id: str
    source_message_ids: list[str] = Field(..., min_length=1)
    confidence: float


class ProjectSummaryOut(BaseModel):
    """List-view row: enough to render a card."""
    id: str
    label: str
    state: str
    confidence: float
    start: datetime
    end: datetime
    member_count: int
    thread_count: int


class ProjectListOut(BaseModel):
    projects: list[ProjectSummaryOut]  # confidence DESC, then label


class ProjectDetailOut(BaseModel):
    id: str
    label: str
    state: str
    confidence: float
    start: datetime
    end: datetime
    metrics: ProjectMetrics
    who_to_ask: list[WhoToAsk]
    members: list[ProjectMemberOut]
    recent_threads: list[RecentThreadOut]
    activity: list[ActivityItemOut] = Field(default_factory=list)  # S4 — from event table
