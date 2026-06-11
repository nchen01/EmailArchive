"""Cover-for-me orchestration (D11/D12, S5 + S7.11 L2 upgrade).

A thin layer between the API router and the existing S4 synthesis functions.
It assembles L1 context (Edge / Threads / Events) from the DB for whichever
entity the router matched, then delegates to ``synthesize_project`` or
``synthesize_contact``. There is NO new model prompt for the L1 paths — the
grounding and citation discipline live in the S4 synthesis functions we reuse.

S7.11 addition: accepts an optional ``l2_hits`` list of RetrievalHit objects.
  - For L1-matched paths: L2 hit content (subject/snippet) is appended as a
    "Retrieved supporting messages" block so the model can see and cite L2
    evidence. The combined allow-list (L1 headers ∪ L2 headers) is enforced.
  - For the L2-only path (no L1 match, l2_hits non-empty): ``_synthesize_l2_hits``
    builds a context from hit subjects/snippets and calls synth_fn.

"No citation, no claim" is enforced in all paths: any claim whose
source_message_ids are not all within the permitted set is dropped before the
result leaves the synthesis layer.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from ekc_schemas import Person, Project

from services.db import mappers
from services.db import models as orm

from .client import make_anthropic_synth_fn
from .contact_summary import (
    build_context as _build_contact_context,
    QUERY as _CONTACT_QUERY,
    SYSTEM_PROMPT as _CONTACT_SYSTEM,
    synthesize_contact,
)
from .contracts import SynthesisClaim, SynthesisResult
from .params import PARAMS, SynthesisParams
from .project_summary import (
    build_context as _build_project_context,
    QUERY as _PROJECT_QUERY,
    SYSTEM_PROMPT as _PROJECT_SYSTEM,
    synthesize_project,
)

if TYPE_CHECKING:
    from services.retrieval.contracts import RetrievalHit

# Intent signal: "who do I ask / who owns / who handles ..."
_WHO_ASK = re.compile(r"who\s+(do\s+i\s+ask|owns?|handles?|is\s+responsible)", re.I)

# System prompt for L2-only synthesis (no L1 entity match available).
_L2_SYSTEM_PROMPT = """\
You write a grounded, cited answer to the user's question, using ONLY the email
evidence provided below. This is a partial record: email is one channel.

Rules — every one is mandatory:
  - Every claim MUST cite the message_id_header value(s) from the provided messages.
    No citation, no claim. Cite only the message_id_header values shown in the
    evidence block.
  - One factual clause per claim, no adjectives.
  - If the evidence does not answer the question, say so explicitly; do not fabricate.
"""


def synthesize_cover_for_me(
    query: str,
    matched_person: Person | None,
    matched_project: Project | None,
    *,
    db: Session,
    mailbox_id: str,
    synth_fn=None,
    params: SynthesisParams = PARAMS,
    l2_hits: "list[RetrievalHit] | None" = None,
) -> tuple[SynthesisResult, str | None]:
    """Route a cover-for-me query to project- or contact-state synthesis.

    Returns ``(result, routed_to_label)`` where ``routed_to_label`` is
    ``"project:<label>"`` or ``"person:<name>"`` or ``None`` (no route).

    S7.11: ``l2_hits`` carries RetrievalHit objects from hybrid_search.
      - For L1-matched paths, l2_hits content is appended to the synthesis
        context and their headers are added to the citation allow-list.
      - For the L2-only path (both matched_* are None, l2_hits non-empty),
        synthesis runs on the L2 hits directly.
    """
    if matched_project is not None:
        if _WHO_ASK.search(query):
            # "Who do I ask about X?" → pure L1 who-to-ask (no model call).
            result = _who_to_ask_for_project(
                matched_project, db=db, mailbox_id=mailbox_id, params=params
            )
        else:
            # "What's the state of X?" → project-state synthesis with L2 support.
            result = _project_state(
                matched_project, db=db, mailbox_id=mailbox_id,
                synth_fn=synth_fn, params=params,
                l2_hits=l2_hits,
            )
        return result, f"project:{matched_project.label}"

    if matched_person is not None:
        result = _who_to_ask(
            matched_person, db=db, mailbox_id=mailbox_id,
            synth_fn=synth_fn, params=params,
            l2_hits=l2_hits,
        )
        name = matched_person.names[0] if matched_person.names else matched_person.canonical_email
        return result, f"person:{name}"

    # No L1 match.
    if l2_hits:
        # L2 is the primary source — synthesize from message evidence.
        result = _synthesize_l2_hits(l2_hits, query, synth_fn=synth_fn, params=params)
        return result, None

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


# ── shared L2 context helper ──────────────────────────────────────────────────

def _l2_context_block(hits: "list[RetrievalHit]") -> str:
    """Build a context block listing L2 hit subjects/snippets for model context."""
    lines = ["Retrieved supporting messages:", ""]
    for hit in hits:
        lines.append(f"[{hit.message_id_header}]")
        lines.append(f"Subject: {hit.subject}")
        lines.append(f"Date: {hit.ts.isoformat()}")
        lines.append(f"Snippet: {hit.snippet}")
        lines.append("")
    return "\n".join(lines)


# ── project state ─────────────────────────────────────────────────────────────

def _project_state(
    project: Project,
    *,
    db: Session,
    mailbox_id: str,
    synth_fn,
    params: SynthesisParams,
    l2_hits: "list[RetrievalHit] | None" = None,
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
    l1_headers = {
        m.message_id_header
        for msgs in messages_by_thread.values()
        for m in msgs
    }
    l2_headers = {h.message_id_header for h in l2_hits} if l2_hits else set()
    allowed_headers = l1_headers | l2_headers

    if not allowed_headers:
        return SynthesisResult(
            claims=[],
            model=params.model,
            usage={},
            state="insufficient structured evidence — no citable message headers found",
        )

    # No L2 evidence — use synthesize_project directly (unchanged S5 path).
    if not l2_hits:
        return synthesize_project(
            project,
            events,
            threads,
            messages_by_thread,
            synth_fn=synth_fn,
            params=params,
            allowed_message_id_headers=l1_headers,
        )

    # L2 hits present — build combined context so the model can see and cite L2
    # evidence alongside L1 events/threads.
    if synth_fn is None:
        synth_fn = make_anthropic_synth_fn(params)
    context = _build_project_context(project, events, threads, messages_by_thread, params)
    context = context + "\n" + _l2_context_block(l2_hits)
    result = synth_fn(_PROJECT_SYSTEM, context, _PROJECT_QUERY)
    result = result.model_copy(update={
        "claims": [
            c for c in result.claims
            if all(h in allowed_headers for h in c.source_message_ids)
        ]
    })
    return result


# ── who to ask (reuse synthesize_contact) ────────────────────────────────────

def _who_to_ask(
    person: Person,
    *,
    db: Session,
    mailbox_id: str,
    synth_fn,
    params: SynthesisParams,
    l2_hits: "list[RetrievalHit] | None" = None,
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
    l1_headers: set[str] = set()
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
        l1_headers = set(header_ts)

        if l1_headers:
            event_rows = list(
                db.execute(
                    select(orm.Event).where(
                        orm.Event.mailbox_id == mailbox_id,
                        orm.Event.source_message_ids.overlap(list(l1_headers)),
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

    l2_headers = {h.message_id_header for h in l2_hits} if l2_hits else set()
    allowed_headers = l1_headers | l2_headers

    # No citable headers from L1 or L2 → short-circuit.
    if not allowed_headers:
        return SynthesisResult(
            claims=[],
            model=params.model,
            usage={},
            state="insufficient structured evidence — no shared message headers found",
        )

    # No L2 evidence — use synthesize_contact directly (unchanged S5 path).
    if not l2_hits:
        return synthesize_contact(
            person,
            edge,
            threads,
            events,
            synth_fn=synth_fn,
            params=params,
            allowed_message_id_headers=l1_headers,
        )

    # L2 hits present — build combined context so the model can see and cite L2
    # evidence alongside the L1 edge/thread/event context.
    if synth_fn is None:
        synth_fn = make_anthropic_synth_fn(params)
    context = _build_contact_context(person, edge, threads, events, params)
    context = context + "\n" + _l2_context_block(l2_hits)
    result = synth_fn(_CONTACT_SYSTEM, context, _CONTACT_QUERY)
    result = result.model_copy(update={
        "claims": [
            c for c in result.claims
            if all(h in allowed_headers for h in c.source_message_ids)
        ]
    })
    return result


# ── L2-only synthesis (no L1 entity match) ───────────────────────────────────

def _synthesize_l2_hits(
    hits: "list[RetrievalHit]",
    query: str,
    *,
    synth_fn,
    params: SynthesisParams,
) -> SynthesisResult:
    """Synthesize an answer from L2 retrieval hits when L1 has no entity match.

    Builds a context block from hit message_id_header / subject / snippet, calls
    synth_fn with _L2_SYSTEM_PROMPT, then filters claims against the L2
    allow-list before returning. "No citation, no claim" applies: any claim
    whose source_message_ids are not all within the set of hit headers is dropped.

    synth_fn is required — the router ensures it is set (503 otherwise) before
    this path is reached.
    """
    allow_list = {h.message_id_header for h in hits}
    context = _l2_context_block(hits)
    result = synth_fn(_L2_SYSTEM_PROMPT, context, query)

    result = result.model_copy(update={
        "claims": [
            c for c in result.claims
            if all(h in allow_list for h in c.source_message_ids)
        ]
    })
    return result


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
