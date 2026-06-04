"""Project-view response DTOs (spec 02 §5).

Serializations derived from ekc_schemas (Project, Person, Edge, Thread). Events /
L3 summaries are S4 — the ``activity`` field is omitted here (spec 02 §5 marks it
``@sprint S4``).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
    activity: list = []  # @sprint S4 — Events deferred; always empty in S3
