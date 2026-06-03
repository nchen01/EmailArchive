"""Network-map response DTOs (spec 05 §5).

These are serializations DERIVED from ``packages.schemas`` (Person, Edge, Thread,
Role) — not a fork. The role *value strings* come straight from
``packages.schemas.Role``; the API never emits display labels or colors (spec 05 §4).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OwnerOut(BaseModel):
    person_id: str
    name: str
    canonical_email: str


class NodeOut(BaseModel):
    person_id: str
    name: str
    canonical_email: str
    role: str
    role_confidence: float
    org_domain: str | None
    weight: float
    message_count: int
    last_contact: datetime


class EdgeOut(BaseModel):
    person_id: str
    weight: float
    message_count: int
    sent_to_count: int
    received_count: int
    first_contact: datetime
    last_contact: datetime


class NetworkMapOut(BaseModel):
    owner: OwnerOut
    nodes: list[NodeOut]  # sorted by weight DESC
    edges: list[EdgeOut]  # sorted by weight DESC


class ThreadSummaryOut(BaseModel):
    thread_id: str
    subject: str
    other_participants: list[str]
    last: datetime
    message_count: int


class PersonDetailOut(BaseModel):
    person_id: str
    name: str
    canonical_email: str
    all_emails: list[str]
    role: str
    role_confidence: float
    org_domain: str | None


class ContactDetailOut(BaseModel):
    person: PersonDetailOut
    edge: EdgeOut
    recent_threads: list[ThreadSummaryOut]  # last 10, t_end desc
