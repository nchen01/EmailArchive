"""Network-map read endpoints (spec 05 §5).

Reads the relationship graph materialized by L1 from Postgres and assembles the
§5.1 (full graph) and §5.2 (contact detail) shapes.

Invariants (spec 05 §5):
- Owner never appears in ``nodes`` / ``edges`` (the graph has no owner Edge).
- Every ``edge.person_id`` has a matching node.
- ``nodes`` and ``edges`` are ordered by ``weight DESC``.
- Identity-merged addresses collapse to one Person → one node (already true in L1).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.db import models as orm

from ..deps import get_db
from ..schemas.network_map import (
    ContactDetailOut,
    EdgeOut,
    NetworkMapOut,
    NodeOut,
    OwnerOut,
    PersonDetailOut,
    ThreadSummaryOut,
)

router = APIRouter(tags=["network-map"])


def _get_mailbox(db: Session, mailbox_id: str) -> orm.Mailbox:
    mbx = db.get(orm.Mailbox, mailbox_id)
    if mbx is None:
        raise HTTPException(status_code=404, detail="mailbox not found")
    return mbx


def _org_domain(org: orm.Org | None) -> str | None:
    if org is None or not org.domains:
        return None
    return org.domains[0]


def _person_name(person: orm.Person) -> str:
    return person.names[0] if person.names else person.canonical_email


def _resolve_person_by_email(db: Session, mailbox_id: str, email: str) -> orm.Person | None:
    """Resolve an email to its Person via the identity table."""
    ident = db.execute(
        select(orm.Identity).where(
            orm.Identity.mailbox_id == mailbox_id, orm.Identity.email == email
        )
    ).scalar_one_or_none()
    if ident is None or ident.person_id is None:
        return None
    return db.get(orm.Person, ident.person_id)


@router.get("/network-map/{mailbox_id}", response_model=NetworkMapOut)
async def get_network_map(
    mailbox_id: str,
    roles: str | None = Query(None, description="Comma-separated Role values"),
    min_weight: float = Query(0.0, ge=0.0),
    db: Session = Depends(get_db),
) -> NetworkMapOut:
    mbx = _get_mailbox(db, mailbox_id)

    # Resolve the owner by identity first; fall back to the mailbox's own
    # owner_person_id link. The fallback lets a mailbox that has an owner Person
    # but not the full L1 identity/edge graph (e.g. the Handoff demo mailbox,
    # which seeds messages/events only) return an EMPTY graph (200) instead of a
    # 404 that makes the workspace look broken. A mailbox with no owner at all
    # still 404s.
    owner_person = _resolve_person_by_email(db, mailbox_id, mbx.owner_email)
    if owner_person is None and mbx.owner_person_id is not None:
        owner_person = db.get(orm.Person, mbx.owner_person_id)
    if owner_person is None:
        raise HTTPException(status_code=404, detail="owner person not resolved")

    role_filter: set[str] | None = None
    if roles:
        role_filter = {r.strip() for r in roles.split(",") if r.strip()}

    # Edges (owner has no edge by construction); join Person for node attributes.
    rows = db.execute(
        select(orm.Edge, orm.Person)
        .join(orm.Person, orm.Edge.person_id == orm.Person.id)
        .where(orm.Edge.mailbox_id == mailbox_id)
        .where(orm.Edge.weight >= min_weight)
        .order_by(orm.Edge.weight.desc(), orm.Person.canonical_email)
    ).all()

    # Pre-load orgs for domain resolution.
    org_ids = {p.org_id for _, p in rows if p.org_id}
    orgs: dict[str, orm.Org] = {}
    if org_ids:
        for org in db.execute(
            select(orm.Org).where(orm.Org.id.in_(org_ids))
        ).scalars():
            orgs[str(org.id)] = org

    nodes: list[NodeOut] = []
    edges: list[EdgeOut] = []
    for edge, person in rows:
        # Owner exclusion is structural, but guard defensively.
        if str(person.id) == str(owner_person.id):
            continue
        if role_filter is not None and person.role not in role_filter:
            continue
        org = orgs.get(str(person.org_id)) if person.org_id else None
        nodes.append(
            NodeOut(
                person_id=str(person.id),
                name=_person_name(person),
                canonical_email=person.canonical_email,
                role=person.role,
                role_confidence=float(person.role_confidence),
                org_domain=_org_domain(org),
                weight=float(edge.weight),
                message_count=edge.message_count,
                last_contact=edge.last_contact,
            )
        )
        edges.append(
            EdgeOut(
                person_id=str(person.id),
                weight=float(edge.weight),
                message_count=edge.message_count,
                sent_to_count=edge.sent_to_count,
                received_count=edge.received_count,
                first_contact=edge.first_contact,
                last_contact=edge.last_contact,
            )
        )

    owner = OwnerOut(
        person_id=str(owner_person.id),
        name=_person_name(owner_person),
        canonical_email=owner_person.canonical_email,
    )
    return NetworkMapOut(owner=owner, nodes=nodes, edges=edges)


@router.get(
    "/network-map/{mailbox_id}/contact/{person_id}",
    response_model=ContactDetailOut,
)
async def get_contact_detail(
    mailbox_id: str,
    person_id: str,
    db: Session = Depends(get_db),
) -> ContactDetailOut:
    mbx = _get_mailbox(db, mailbox_id)

    person = db.get(orm.Person, person_id)
    if person is None or str(person.mailbox_id) != str(mailbox_id):
        raise HTTPException(status_code=404, detail="contact not found")

    edge_row = db.execute(
        select(orm.Edge).where(
            orm.Edge.mailbox_id == mailbox_id, orm.Edge.person_id == person_id
        )
    ).scalar_one_or_none()
    if edge_row is None:
        raise HTTPException(status_code=404, detail="no relationship edge for contact")

    org = db.get(orm.Org, person.org_id) if person.org_id else None

    # all_emails = the identities resolved to this person.
    all_emails = sorted(
        db.execute(
            select(orm.Identity.email).where(
                orm.Identity.mailbox_id == mailbox_id,
                orm.Identity.person_id == person_id,
            )
        ).scalars()
    )

    person_out = PersonDetailOut(
        person_id=str(person.id),
        name=_person_name(person),
        canonical_email=person.canonical_email,
        all_emails=all_emails,
        role=person.role,
        role_confidence=float(person.role_confidence),
        org_domain=_org_domain(org),
    )

    edge_out = EdgeOut(
        person_id=str(person.id),
        weight=float(edge_row.weight),
        message_count=edge_row.message_count,
        sent_to_count=edge_row.sent_to_count,
        received_count=edge_row.received_count,
        first_contact=edge_row.first_contact,
        last_contact=edge_row.last_contact,
    )

    # Recent shared threads: both owner and contact in thread.participants.
    owner_email = mbx.owner_email
    contact_emails = set(all_emails) or {person.canonical_email}

    # Single query: threads where both owner and contact appear, message count via GROUP BY.
    # .contains([x]) → SQL @>; .overlap(list) → SQL &&.
    stmt = (
        select(orm.Thread, func.count(orm.Message.id).label("msg_count"))
        .outerjoin(
            orm.Message,
            (orm.Message.thread_id == orm.Thread.id)
            & (orm.Message.mailbox_id == mailbox_id),
        )
        .where(orm.Thread.mailbox_id == mailbox_id)
        .where(orm.Thread.participants.contains([owner_email]))
        .where(orm.Thread.participants.overlap(sorted(contact_emails)))
        .group_by(orm.Thread.id)
        .order_by(orm.Thread.t_end.desc())
        .limit(10)
    )

    recent: list[ThreadSummaryOut] = []
    for t, msg_count in db.execute(stmt):
        parts = set(t.participants)
        others = sorted(parts - {owner_email} - contact_emails)
        recent.append(
            ThreadSummaryOut(
                thread_id=str(t.id),
                subject=t.subject_norm,
                other_participants=others,
                last=t.t_end,
                message_count=msg_count,
            )
        )

    return ContactDetailOut(person=person_out, edge=edge_out, recent_threads=recent)
