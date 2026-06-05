"""S5 cover-for-me orchestration (D11, implementation-plan §6.3).

A thin layer between the API router and the existing S4 synthesis functions.
It assembles L1 context (Edge / Threads / Events) from the DB for whichever
entity the router matched, then delegates to ``synthesize_project`` or
``synthesize_contact``. There is NO new model prompt here — the grounding and
citation discipline live in the S4 synthesis functions we reuse.

Bounded by design (D11): this routes over structured L1 objects only — Person,
Project, Event, Edge, Thread already in Postgres. No vector retrieval / L2. When
nothing matches, the router returns an "insufficient structured evidence" result
without ever calling the model.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ekc_schemas import Person, Project

from services.db import mappers
from services.db import models as orm

from .contact_summary import synthesize_contact
from .contracts import SynthesisClaim, SynthesisResult
from .params import PARAMS, SynthesisParams
from .project_summary import synthesize_project

# Intent signal: "who do I ask / who owns / who handles ..."
_WHO_ASK = re.compile(r"who\s+(do\s+i\s+ask|owns?|handles?|is\s+responsible)", re.I)


def synthesize_cover_for_me(
    query: str,
    matched_person: Person | None,
    matched_project: Project | None,
    *,
    db: Session,
    mailbox_id: str,
    synth_fn=None,
    params: SynthesisParams = PARAMS,
) -> tuple[SynthesisResult, str | None]:
    """Route a cover-for-me query to project- or contact-state synthesis.

    Returns ``(result, routed_to_label)`` where ``routed_to_label`` is
    ``"project:<label>"`` or ``"person:<name>"`` or ``None`` (no route).

    The router decides which entity (if any) matched; this function only
    assembles the L1 context and delegates. ``synth_fn`` is injected so tests
    stay offline. With neither entity matched, no model call is made.
    """
    if matched_project is not None:
        if _WHO_ASK.search(query):
            # "Who do I ask about X?" → pure L1 who-to-ask (no model call).
            result = _who_to_ask_for_project(
                matched_project, db=db, mailbox_id=mailbox_id, params=params
            )
        else:
            # "What's the state of X?" → project-state synthesis.
            result = _project_state(
                matched_project, db=db, mailbox_id=mailbox_id, synth_fn=synth_fn, params=params
            )
        return result, f"project:{matched_project.label}"

    if matched_person is not None:
        result = _who_to_ask(
            matched_person, db=db, mailbox_id=mailbox_id, synth_fn=synth_fn, params=params
        )
        name = matched_person.names[0] if matched_person.names else matched_person.canonical_email
        return result, f"person:{name}"

    return (
        SynthesisResult(
            claims=[],
            model=params.model,
            usage={},
            state=(
                "insufficient structured evidence — rephrase mentioning a "
                "project or contact name"
            ),
        ),
        None,
    )


# ── who to ask about a project (pure L1, no model call) ─────────────────────

def _who_to_ask_for_project(
    project: Project,
    *,
    db: Session,
    mailbox_id: str,
    params: SynthesisParams,
    top_n: int = 3,
) -> SynthesisResult:
    """Return the top contacts for a project, cited by messages they sent.

    Pure L1 — deterministic, no model call (D11). Each claim names a contact
    and is grounded by a message_id_header from a message they sent inside
    one of the project's threads.
    """
    member_rows = db.execute(
        select(orm.ProjectMember)
        .where(orm.ProjectMember.project_id == project.id)
        .order_by(orm.ProjectMember.involvement.desc())
        .limit(top_n)
    ).scalars().all()

    if not member_rows:
        return SynthesisResult(
            claims=[],
            model=params.model,
            usage={},
            state="insufficient structured evidence — project has no members",
        )

    thread_ids = [
        tid
        for (tid,) in db.execute(
            select(orm.ThreadProjectAssignment.thread_id).where(
                orm.ThreadProjectAssignment.project_id == project.id
            )
        )
    ]

    claims: list[SynthesisClaim] = []
    for m in member_rows:
        person = db.get(orm.Person, m.person_id)
        if not person:
            continue

        person_emails = [
            e
            for (e,) in db.execute(
                select(orm.Identity.email).where(
                    orm.Identity.mailbox_id == mailbox_id,
                    orm.Identity.person_id == m.person_id,
                )
            )
        ] or [person.canonical_email]

        # Citations: messages this person sent inside the project's threads.
        cited_headers: list[str] = []
        if thread_ids:
            cited_headers = [
                h
                for (h,) in db.execute(
                    select(orm.Message.message_id_header).where(
                        orm.Message.mailbox_id == mailbox_id,
                        orm.Message.thread_id.in_(thread_ids),
                        orm.Message.sender_email.in_(person_emails),
                    ).limit(3)
                )
            ]

        if not cited_headers:
            continue  # precision over recall: skip if no citable evidence

        name = person.names[0] if person.names else person.canonical_email
        role = person.role  # ORM column is a plain string, not an enum
        claims.append(
            SynthesisClaim(
                text=f"Ask {name} ({role}) — citable project participation found.",
                source_message_ids=cited_headers,
            )
        )

    if not claims:
        return SynthesisResult(
            claims=[],
            model=params.model,
            usage={},
            state="insufficient structured evidence — no citable messages from project members",
        )

    return SynthesisResult(claims=claims, model=params.model, usage={})


# ── project state (reuse synthesize_project) ─────────────────────────────────

def _project_state(
    project: Project,
    *,
    db: Session,
    mailbox_id: str,
    synth_fn,
    params: SynthesisParams,
) -> SynthesisResult:
    event_rows = list(
        db.execute(
            select(orm.Event).where(
                orm.Event.mailbox_id == mailbox_id,
                orm.Event.project_id == project.id,
            )
        ).scalars()
    )
    events = [mappers.row_to_event(e) for e in event_rows]

    thread_ids = sorted(
        {
            tid
            for (tid,) in db.execute(
                select(orm.ThreadProjectAssignment.thread_id).where(
                    orm.ThreadProjectAssignment.project_id == project.id
                )
            )
        }
    )
    threads = _threads_for_ids(db, thread_ids)
    messages_by_thread = _messages_by_thread(db, mailbox_id, thread_ids)
    allowed_headers = {
        m.message_id_header
        for msgs in messages_by_thread.values()
        for m in msgs
    }

    # No citable headers → nothing to ground claims against; short-circuit.
    if not allowed_headers:
        return SynthesisResult(
            claims=[],
            model=params.model,
            usage={},
            state="insufficient structured evidence — no citable message headers found",
        )

    return synthesize_project(
        project,
        events,
        threads,
        messages_by_thread,
        synth_fn=synth_fn,
        params=params,
        allowed_message_id_headers=allowed_headers,  # always a non-empty set here
    )


# ── who to ask (reuse synthesize_contact) ────────────────────────────────────

def _who_to_ask(
    person: Person,
    *,
    db: Session,
    mailbox_id: str,
    synth_fn,
    params: SynthesisParams,
) -> SynthesisResult:
    edge_row = db.execute(
        select(orm.Edge).where(
            orm.Edge.mailbox_id == mailbox_id, orm.Edge.person_id == person.id
        )
    ).scalar_one_or_none()
    if edge_row is None:
        # No relationship edge → no structural context to ground a contact answer.
        return SynthesisResult(
            claims=[],
            model=params.model,
            usage={},
            state="insufficient structured evidence — no relationship data for this contact",
        )
    edge = mappers.row_to_edge(edge_row)

    mbx = db.get(orm.Mailbox, mailbox_id)
    owner_email = mbx.owner_email if mbx else ""

    contact_emails = sorted(
        db.execute(
            select(orm.Identity.email).where(
                orm.Identity.mailbox_id == mailbox_id,
                orm.Identity.person_id == person.id,
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
            .limit(params.max_context_messages)
        ).scalars()
    )
    threads = [mappers.row_to_thread(t) for t in thread_rows]
    thread_ids = [str(t.id) for t in thread_rows]

    events = []
    allowed_headers: set[str] = set()
    if thread_ids:
        msg_rows = list(
            db.execute(
                select(orm.Message.message_id_header, orm.Message.ts).where(
                    orm.Message.mailbox_id == mailbox_id,
                    orm.Message.thread_id.in_(thread_ids),
                )
            ).all()
        )
        header_ts: dict = {row[0]: row[1] for row in msg_rows}
        allowed_headers = set(header_ts)

        if allowed_headers:
            event_rows = list(
                db.execute(
                    select(orm.Event).where(
                        orm.Event.mailbox_id == mailbox_id,
                        orm.Event.source_message_ids.overlap(list(allowed_headers)),
                    )
                ).scalars()
            )
            raw_events = [mappers.row_to_event(e) for e in event_rows]

            def _max_ts(ev) -> datetime:
                ts_list = [header_ts[h] for h in ev.source_message_ids if h in header_ts]
                if not ts_list:
                    return datetime.min.replace(tzinfo=timezone.utc)
                ts = max(ts_list)
                return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

            events = sorted(raw_events, key=_max_ts, reverse=True)

    # No citable headers → short-circuit; every claim would be filtered anyway.
    if not allowed_headers:
        return SynthesisResult(
            claims=[],
            model=params.model,
            usage={},
            state="insufficient structured evidence — no shared message headers found",
        )

    return synthesize_contact(
        person,
        edge,
        threads,
        events,
        synth_fn=synth_fn,
        params=params,
        allowed_message_id_headers=allowed_headers,  # always a non-empty set here
    )


# ── shared DB helpers (same shape as routers/synthesis.py) ───────────────────

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
