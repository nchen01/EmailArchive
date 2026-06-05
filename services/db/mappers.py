"""Pydantic ↔ ORM row conversion (spec 04 ticket 4.7).

Two directions per model. The ``*_to_row`` functions return plain column dicts
(used by the upsert statements in ``store.py``); the ``row_to_*`` functions
reconstruct the authoritative ``ekc_schemas`` objects from ORM rows.

Design notes
------------
- Address objects (``Message.sender``/``to``/``cc``) are serialized into the
  ``message.addresses`` JSONB column (spec 04 §4). The flat ``sender_email`` /
  ``to_emails`` / ``cc_emails`` columns are denormalized for indexing/filtering.
- Attachments are stored as child ``message_attachment`` rows, not on the message.
- ``Person.org_id`` round-trips as-is; the API derives ``org_domain`` from the
  ``org`` table separately.
"""
from __future__ import annotations

from ekc_schemas import (
    Address,
    AttachmentRef,
    Edge,
    Event,
    EventType,
    Identity,
    LabelSource,
    Message,
    Org,
    Person,
    Project,
    ProjectMember,
    Role,
    Sensitivity,
    Thread,
    ThreadProjectAssignment,
)

from . import models as orm


# ── Address (embedded in message.addresses jsonb) ────────────────────────────

def _address_to_dict(a: Address) -> dict:
    return {"raw": a.raw, "email": a.email, "display_names": list(a.display_names)}


def _dict_to_address(d: dict) -> Address:
    return Address(
        raw=d["raw"], email=d["email"], display_names=list(d.get("display_names", []))
    )


# ── Message ──────────────────────────────────────────────────────────────────

def message_to_row(msg: Message, mailbox_id: str) -> dict:
    return {
        "id": msg.id,
        "mailbox_id": mailbox_id,
        "message_id_header": msg.message_id_header,
        "provider_id": msg.provider_id,
        "thread_id": msg.thread_id,
        "sender_email": msg.sender.email,
        "to_emails": [a.email for a in msg.to],
        "cc_emails": [a.email for a in msg.cc],
        "addresses": {
            "sender": _address_to_dict(msg.sender),
            "to": [_address_to_dict(a) for a in msg.to],
            "cc": [_address_to_dict(a) for a in msg.cc],
        },
        "ts": msg.ts,
        "subject": msg.subject,
        "clean_text": msg.clean_text,
        "link_domains": list(msg.link_domains),
        "sensitivity": [s.value for s in msg.sensitivity],
        "noise": msg.noise,
        "raw_uri": msg.raw_uri,
    }


def attachment_rows(msg: Message) -> list[dict]:
    return [
        {
            "message_id": msg.id,
            "sha256": att.sha256,
            "filename": att.filename,
            "mimetype": att.mimetype,
            "size_bytes": att.size_bytes,
        }
        for att in msg.attachment_refs
    ]


def row_to_message(
    row: orm.Message, attachments: list[orm.MessageAttachment] | None = None
) -> Message:
    addrs = row.addresses or {}
    sender = _dict_to_address(addrs["sender"])
    to = [_dict_to_address(a) for a in addrs.get("to", [])]
    cc = [_dict_to_address(a) for a in addrs.get("cc", [])]
    refs = [
        AttachmentRef(
            sha256=a.sha256,
            filename=a.filename,
            mimetype=a.mimetype,
            size_bytes=a.size_bytes,
        )
        for a in (attachments or [])
    ]
    return Message(
        id=str(row.id),
        message_id_header=row.message_id_header,
        provider_id=row.provider_id,
        thread_id=str(row.thread_id),
        sender=sender,
        to=to,
        cc=cc,
        ts=row.ts,
        subject=row.subject,
        clean_text=row.clean_text,
        attachment_refs=refs,
        link_domains=list(row.link_domains),
        sensitivity=[Sensitivity(s) for s in row.sensitivity],
        noise=row.noise,
        raw_uri=row.raw_uri,
    )


# ── Thread ───────────────────────────────────────────────────────────────────

def thread_to_row(thread: Thread, mailbox_id: str) -> dict:
    return {
        "id": thread.id,
        "mailbox_id": mailbox_id,
        "provider_thread_ids": list(thread.provider_thread_ids),
        "root_message_id_header": thread.root_message_id_header,
        "subject_norm": thread.subject_norm,
        "participants": list(thread.participants),
        "t_start": thread.t_start,
        "t_end": thread.t_end,
        "lineage_conflict": thread.lineage_conflict,
    }


def row_to_thread(row: orm.Thread, message_ids: list[str] | None = None) -> Thread:
    return Thread(
        id=str(row.id),
        provider_thread_ids=list(row.provider_thread_ids),
        root_message_id_header=row.root_message_id_header,
        message_ids=list(message_ids or []),
        subject_norm=row.subject_norm,
        participants=list(row.participants),
        t_start=row.t_start,
        t_end=row.t_end,
        lineage_conflict=row.lineage_conflict,
    )


# ── Org ──────────────────────────────────────────────────────────────────────

def org_to_row(org: Org, mailbox_id: str) -> dict:
    return {
        "id": org.id,
        "mailbox_id": mailbox_id,
        "name": org.name,
        "domains": list(org.domains),
        "internal": org.internal,
    }


def row_to_org(row: orm.Org) -> Org:
    return Org(
        id=str(row.id),
        name=row.name,
        domains=list(row.domains),
        internal=row.internal,
    )


# ── Person ───────────────────────────────────────────────────────────────────

def person_to_row(person: Person, mailbox_id: str) -> dict:
    return {
        "id": person.id,
        "mailbox_id": mailbox_id,
        "canonical_email": person.canonical_email,
        "names": list(person.names),
        "org_id": person.org_id,
        "role": person.role.value,
        "role_confidence": person.role_confidence,
    }


def row_to_person(row: orm.Person, identities: list[str] | None = None) -> Person:
    return Person(
        id=str(row.id),
        canonical_email=row.canonical_email,
        names=list(row.names),
        org_id=str(row.org_id) if row.org_id else None,
        role=Role(row.role),
        role_confidence=float(row.role_confidence),
        identities=list(identities or []),
    )


# ── Identity ─────────────────────────────────────────────────────────────────

def identity_to_row(identity: Identity, mailbox_id: str) -> dict:
    return {
        "mailbox_id": mailbox_id,
        "email": identity.email,
        "display_names": list(identity.display_names),
        "person_id": identity.person_id,
    }


def row_to_identity(row: orm.Identity) -> Identity:
    return Identity(
        email=row.email,
        display_names=list(row.display_names),
        person_id=str(row.person_id) if row.person_id else None,
    )


# ── Edge ─────────────────────────────────────────────────────────────────────

def edge_to_row(edge: Edge, mailbox_id: str) -> dict:
    return {
        "mailbox_id": mailbox_id,
        "person_id": edge.person_id,
        "message_count": edge.message_count,
        "sent_to_count": edge.sent_to_count,
        "received_count": edge.received_count,
        "first_contact": edge.first_contact,
        "last_contact": edge.last_contact,
        "weight": edge.weight,
    }


def row_to_edge(row: orm.Edge) -> Edge:
    return Edge(
        person_id=str(row.person_id),
        message_count=row.message_count,
        sent_to_count=row.sent_to_count,
        received_count=row.received_count,
        first_contact=row.first_contact,
        last_contact=row.last_contact,
        weight=float(row.weight),
    )


# ── Project / members / assignments (spec 03) ────────────────────────────────

def project_to_row(project: Project, mailbox_id: str) -> dict:
    return {
        "id": project.id,
        "mailbox_id": mailbox_id,
        "label": project.label,
        "label_source": project.label_source.value,
        "start": project.start,
        "end": project.end,
        "confidence": project.confidence,
        "debug": project.debug,
    }


def project_member_rows(project: Project) -> list[dict]:
    return [
        {
            "project_id": project.id,
            "person_id": m.person_id,
            "involvement": m.involvement,
            "message_count": m.message_count,
        }
        for m in project.members
    ]


def assignment_to_row(a: ThreadProjectAssignment) -> dict:
    return {
        "thread_id": a.thread_id,
        "project_id": a.project_id,
        "weight": a.weight,
        "is_primary": a.is_primary,
    }


# ── Event (spec 01 §7, S4) ───────────────────────────────────────────────────

def event_to_row(event: Event, mailbox_id: str) -> dict:
    return {
        "id": event.id,
        "mailbox_id": mailbox_id,
        "actor_person_id": event.actor_person_id,
        "type": event.type.value,
        "summary": event.summary,
        "project_id": event.project_id,
        "source_message_ids": list(event.source_message_ids),
        "confidence": event.confidence,
    }


def row_to_event(row: orm.Event) -> Event:
    return Event(
        id=str(row.id),
        actor_person_id=str(row.actor_person_id),
        type=EventType(row.type),
        summary=row.summary,
        project_id=str(row.project_id) if row.project_id else None,
        source_message_ids=list(row.source_message_ids),
        confidence=float(row.confidence),
    )


def row_to_project(
    row: orm.Project, members: list[orm.ProjectMember] | None = None
) -> Project:
    member_models = [
        ProjectMember(
            person_id=str(m.person_id),
            involvement=float(m.involvement),
            message_count=m.message_count,
        )
        for m in sorted(members or [], key=lambda m: (-float(m.involvement), str(m.person_id)))
    ]
    return Project(
        id=str(row.id),
        label=row.label,
        label_source=LabelSource(row.label_source),
        member_ids=[m.person_id for m in member_models],
        members=member_models,
        thread_ids=[],  # filled by the caller from assignment rows if needed
        start=row.start,
        end=row.end,
        confidence=float(row.confidence),
        debug=row.debug,
    )
