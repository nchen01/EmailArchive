"""S5 cover-for-me query endpoint (D11, implementation-plan §6.3).

One POST surface — the third and last MVP surface:

  POST /api/cover-for-me/{mailbox_id}   body {"query": "..."}

Bounded, L1-only (D11): the free-text query is matched against known Person
names and Project labels already in Postgres, routed to the right structured
context, and answered by the reused S4 synthesis functions (grounded, cited).
There is NO vector retrieval / L2 here. A query that matches no entity returns a
clear "insufficient structured evidence" result — never a fabricated answer.

Behaviour (same contract as routers/synthesis.py):
  - 503 when the Anthropic API key is absent (only when a route actually needs
    the model — the fallback short-circuits before any key check).
  - 404 when the mailbox does not exist.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ekc_schemas import Person, Project

from services.db import mappers
from services.db import models as orm
from services.synthesis.client import MissingApiKeyError, make_anthropic_synth_fn
from services.synthesis.cover_for_me import synthesize_cover_for_me
from services.synthesis.params import PARAMS

from ..deps import get_db
from ..schemas.cover_for_me import CoverForMeRequest, CoverForMeResponse

router = APIRouter(tags=["cover-for-me"])

# Routing signals — a quick keyword check on the query string (not ML).
WHO_ASK = re.compile(r"who\s+(do\s+i\s+ask|owns?|handles?|is\s+responsible)", re.I)
WHAT_STATE = re.compile(r"(state|status|what.s?\s+been\s+done|what\s+happened)", re.I)

# Minimum token length for entity matching — prevents false-matches on short/common
# tokens (e.g. "at", "or", "of") that could appear inside unrelated words.
_MIN_MATCH_LEN = 3


def _token_match(name: str, query_lower: str) -> bool:
    """True when ``name`` appears as a distinct word in ``query_lower`` (word-boundary).

    Word-boundary regex (\b) prevents "Ben" from matching "benefits" or
    "Dana" from matching "Danaher". Combined with _MIN_MATCH_LEN, this avoids
    accidental substring false-positives on very short tokens.
    """
    if not name or len(name) < _MIN_MATCH_LEN:
        return False
    pattern = r"\b" + re.escape(name.lower()) + r"\b"
    return bool(re.search(pattern, query_lower))


def _get_mailbox(db: Session, mailbox_id: str) -> orm.Mailbox:
    mbx = db.get(orm.Mailbox, mailbox_id)
    if mbx is None:
        raise HTTPException(status_code=404, detail="mailbox not found")
    return mbx


def _match_persons(query: str, db: Session, mailbox_id: str) -> list[orm.Person]:
    """Case-insensitive substring match against person.names array.

    Excludes the mailbox owner (implicit; no edge to self — privacy/§4 owner
    convention)."""
    q = query.lower()
    mbx = db.get(orm.Mailbox, mailbox_id)
    owner_email = (mbx.owner_email or "").lower() if mbx else ""
    persons = db.execute(
        select(orm.Person).where(orm.Person.mailbox_id == mailbox_id)
    ).scalars()
    out: list[orm.Person] = []
    for p in persons:
        if owner_email and (p.canonical_email or "").lower() == owner_email:
            continue
        if any(_token_match(n, q) for n in p.names):
            out.append(p)
    return out


def _match_projects(query: str, db: Session, mailbox_id: str) -> list[orm.Project]:
    """Case-insensitive substring match against project.label."""
    q = query.lower()
    projects = db.execute(
        select(orm.Project).where(orm.Project.mailbox_id == mailbox_id)
    ).scalars()
    return [p for p in projects if _token_match(p.label, q)]


def _best_person(query: str, persons: list[orm.Person]) -> orm.Person | None:
    """The person whose longest matched name covers the most characters."""
    q = query.lower()
    best: orm.Person | None = None
    best_len = -1
    for p in persons:
        matched = [len(n) for n in p.names if _token_match(n, q)]
        score = max(matched) if matched else 0
        if score > best_len:
            best_len, best = score, p
    return best


def _route(
    query: str, db: Session, mailbox_id: str
) -> tuple[Person | None, Project | None]:
    """Resolve the query to (person, project) ekc_schemas objects, or (None, None).

    Routing (D11):
      - Project match + WHAT_STATE / any project question → project.
      - Person match + WHO_ASK → person.
      - Project match alone → project (the common case).
      - Person match alone → person.
      - Multiple matches → most specific (most characters matched); tie → project.
    """
    project_rows = _match_projects(query, db, mailbox_id)
    person_rows = _match_persons(query, db, mailbox_id)

    best_project = None
    if project_rows:
        # Longest label is the most specific project match.
        best_project = max(project_rows, key=lambda p: len(p.label or ""))
    best_person = _best_person(query, person_rows) if person_rows else None

    if best_project is None and best_person is None:
        return None, None

    if best_project is not None and best_person is not None:
        proj_len = len(best_project.label or "")
        pers_len = max(
            (len(n) for n in best_person.names if _token_match(n, query.lower())),
            default=0,
        )
        # Tie or project longer → prefer project.
        if proj_len >= pers_len:
            best_person = None
        else:
            best_project = None

    if best_project is not None:
        members = list(
            db.execute(
                select(orm.ProjectMember).where(
                    orm.ProjectMember.project_id == best_project.id
                )
            ).scalars()
        )
        return None, mappers.row_to_project(best_project, members)

    return mappers.row_to_person(best_person), None


@router.post("/cover-for-me/{mailbox_id}", response_model=CoverForMeResponse)
async def cover_for_me_endpoint(
    mailbox_id: str,
    body: CoverForMeRequest,
    db: Session = Depends(get_db),
) -> CoverForMeResponse:
    _get_mailbox(db, mailbox_id)

    matched_person, matched_project = _route(body.query, db, mailbox_id)

    # No entity matched → fallback result; no model call, no key check (D11).
    if matched_person is None and matched_project is None:
        result, routed_to = synthesize_cover_for_me(
            body.query, None, None, db=db, mailbox_id=mailbox_id, synth_fn=None
        )
        return CoverForMeResponse(query=body.query, routed_to=routed_to, result=result)

    try:
        synth_fn = make_anthropic_synth_fn(PARAMS)
    except MissingApiKeyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    result, routed_to = synthesize_cover_for_me(
        body.query,
        matched_person,
        matched_project,
        db=db,
        mailbox_id=mailbox_id,
        synth_fn=synth_fn,
    )
    return CoverForMeResponse(query=body.query, routed_to=routed_to, result=result)
