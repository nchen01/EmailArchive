"""L3 synthesis endpoints (S4 ticket 4.9, spec 02 §6 + spec 05 §3.4).

Two POST surfaces, each triggering one user-initiated L3 call (no batching):
  POST /api/synthesis/{mailbox_id}/project/{project_id}
  POST /api/synthesis/{mailbox_id}/contact/{person_id}

Behaviour:
  - 503 (not 500) when the Anthropic API key is absent.
  - 404 when the mailbox/project/person does not exist.
  - Empty-events project → empty/"no evidenced activity" result, not an error.

The endpoints assemble context from L1 objects (Events + Threads + Person/Edge)
— Track A output — and hand it to the synthesis layer (Track B). The model call
stays behind the synthesis ``synth_fn``; a missing key raises before any network.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.db import mappers
from services.db import models as orm
from services.synthesis.client import MissingApiKeyError, make_anthropic_synth_fn
from services.synthesis.contact_summary import synthesize_contact
from services.synthesis.contracts import SynthesisResult
from services.synthesis.params import PARAMS
from services.synthesis.project_summary import synthesize_project

from ..deps import get_db

router = APIRouter(tags=["synthesis"])


def _get_mailbox(db: Session, mailbox_id: str) -> orm.Mailbox:
    mbx = db.get(orm.Mailbox, mailbox_id)
    if mbx is None:
        raise HTTPException(status_code=404, detail="mailbox not found")
    return mbx


def _threads_for_ids(db: Session, thread_ids: list[str]) -> list:
    if not thread_ids:
        return []
    rows = list(
        db.execute(
            select(orm.Thread)
            .where(orm.Thread.id.in_(thread_ids))
            .order_by(orm.Thread.t_end.desc())
        ).scalars()
    )
    return [mappers.row_to_thread(r) for r in rows]


def _messages_by_thread(db: Session, mailbox_id: str, thread_ids: list[str]) -> dict:
    by_thread: dict[str, list] = {}
    if not thread_ids:
        return by_thread
    rows = db.execute(
        select(orm.Message).where(
            orm.Message.mailbox_id == mailbox_id,
            orm.Message.thread_id.in_(thread_ids),
        )
    ).scalars()
    for r in rows:
        by_thread.setdefault(str(r.thread_id), []).append(mappers.row_to_message(r))
    return by_thread


@router.post(
    "/synthesis/{mailbox_id}/project/{project_id}",
    response_model=SynthesisResult,
)
async def synthesize_project_endpoint(
    mailbox_id: str, project_id: str, db: Session = Depends(get_db)
) -> SynthesisResult:
    _get_mailbox(db, mailbox_id)

    project_row = db.get(orm.Project, project_id)
    if project_row is None or str(project_row.mailbox_id) != str(mailbox_id):
        raise HTTPException(status_code=404, detail="project not found")

    members = list(
        db.execute(
            select(orm.ProjectMember).where(orm.ProjectMember.project_id == project_id)
        ).scalars()
    )
    project = mappers.row_to_project(project_row, members)

    event_rows = list(
        db.execute(
            select(orm.Event).where(
                orm.Event.mailbox_id == mailbox_id,
                orm.Event.project_id == project_id,
            )
        ).scalars()
    )
    events = [mappers.row_to_event(e) for e in event_rows]

    thread_ids = sorted(
        {
            tid
            for (tid,) in db.execute(
                select(orm.ThreadProjectAssignment.thread_id).where(
                    orm.ThreadProjectAssignment.project_id == project_id
                )
            )
        }
    )
    threads = _threads_for_ids(db, thread_ids)
    messages_by_thread = _messages_by_thread(db, mailbox_id, thread_ids)

    # Build the production synth_fn only if there is something to synthesize.
    # Empty events AND empty threads → graceful empty result (no key needed).
    synth_fn = None
    if events or threads:
        try:
            synth_fn = make_anthropic_synth_fn(PARAMS)
        except MissingApiKeyError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return synthesize_project(
        project, events, threads, messages_by_thread, synth_fn=synth_fn
    )


@router.post(
    "/synthesis/{mailbox_id}/contact/{person_id}",
    response_model=SynthesisResult,
)
async def synthesize_contact_endpoint(
    mailbox_id: str, person_id: str, db: Session = Depends(get_db)
) -> SynthesisResult:
    mbx = _get_mailbox(db, mailbox_id)

    person_row = db.get(orm.Person, person_id)
    if person_row is None or str(person_row.mailbox_id) != str(mailbox_id):
        raise HTTPException(status_code=404, detail="contact not found")
    person = mappers.row_to_person(person_row)

    edge_row = db.execute(
        select(orm.Edge).where(
            orm.Edge.mailbox_id == mailbox_id, orm.Edge.person_id == person_id
        )
    ).scalar_one_or_none()
    if edge_row is None:
        raise HTTPException(status_code=404, detail="no relationship edge for contact")
    edge = mappers.row_to_edge(edge_row)

    # Shared threads: both owner and contact participate.
    owner_email = mbx.owner_email
    contact_emails = sorted(
        db.execute(
            select(orm.Identity.email).where(
                orm.Identity.mailbox_id == mailbox_id,
                orm.Identity.person_id == person_id,
            )
        ).scalars()
    ) or [person.canonical_email]

    thread_rows = list(
        db.execute(
            select(orm.Thread)
            .where(orm.Thread.mailbox_id == mailbox_id)
            .where(orm.Thread.participants.contains([owner_email]))
            .where(orm.Thread.participants.overlap(contact_emails))
            .order_by(orm.Thread.t_end.desc())
            .limit(PARAMS.max_context_messages)
        ).scalars()
    )
    threads = [mappers.row_to_thread(t) for t in thread_rows]
    thread_ids = [str(t.id) for t in thread_rows]

    # Events from those threads: an Event whose citations overlap the threads'
    # message headers. Resolve thread → headers, then match.
    events = []
    if thread_ids:
        headers = [
            h
            for (h,) in db.execute(
                select(orm.Message.message_id_header).where(
                    orm.Message.mailbox_id == mailbox_id,
                    orm.Message.thread_id.in_(thread_ids),
                )
            )
        ]
        if headers:
            event_rows = list(
                db.execute(
                    select(orm.Event).where(
                        orm.Event.mailbox_id == mailbox_id,
                        orm.Event.source_message_ids.overlap(headers),
                    )
                ).scalars()
            )
            events = [mappers.row_to_event(e) for e in event_rows]

    try:
        synth_fn = make_anthropic_synth_fn(PARAMS)
    except MissingApiKeyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return synthesize_contact(person, edge, threads, events, synth_fn=synth_fn)
