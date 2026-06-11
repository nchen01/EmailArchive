"""Cover-for-me query endpoint (D11/D12, S5 surface + S7.11 L2 upgrade).

One POST surface — the third MVP surface:

  POST /api/cover-for-me/{mailbox_id}   body {"query": "..."}

S7.11 routing (Q5 resolved):
  query ──► L1 entity detection (Person / Project)
             │
             ├──── always ──► L2 hybrid search (when embed client available)
             │
        L1 match? ──yes──► L1 primary evidence + L2 headers added to allow-list
             │
            no ──────────► L2 hits? ──yes──► L2 primary synthesis
                                    │
                                   no ───────► insufficient evidence (no model call)

Special case: WHO_ASK + project (pure L1, no model call) — L2 is not used here
because the response is contact-centric (who to ask), not message evidence-centric.

Behaviour (unchanged from S5 contract):
  - 503 when the Anthropic API key is absent and a model call is required.
  - 404 when the mailbox does not exist.
  - No model call → no key check for the insufficient-evidence and
    WHO_ASK+project paths.
  - L2 silently disabled (L1-only) when VOYAGE_API_KEY is not set.
"""
from __future__ import annotations

import logging
import os
import re

_log = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ekc_schemas import Person, Project

from services.db import mappers
from services.db import models as orm
from services.retrieval.contracts import InsufficientEvidence, RetrievalHit
from services.retrieval.hybrid import hybrid_search
from services.retrieval.params import RetrievalParams
from services.synthesis.client import MissingApiKeyError, make_anthropic_synth_fn
from services.synthesis.cover_for_me import synthesize_cover_for_me
from services.synthesis.params import PARAMS

from ..deps import get_db
from ..schemas.cover_for_me import CoverForMeRequest, CoverForMeResponse

router = APIRouter(tags=["cover-for-me"])

# Retrieval params for cover-for-me: cap supporting evidence at 5 hits.
# min_vector_score=0.60 matches the production default; tests override via
# monkeypatching _CFM_RETRIEVAL_PARAMS.
_CFM_RETRIEVAL_PARAMS = RetrievalParams(
    vector_top_k=20,
    fts_top_k=20,
    rerank_top_k=5,
    min_vector_score=0.60,
    min_fts_score=0.0,
)

# Routing signals — a quick keyword check on the query string (not ML).
WHO_ASK = re.compile(r"who\s+(do\s+i\s+ask|owns?|handles?|is\s+responsible)", re.I)
WHAT_STATE = re.compile(r"(state|status|what.s?\s+been\s+done|what\s+happened)", re.I)

# Minimum token length for entity matching — prevents false-matches on short/common
# tokens (e.g. "at", "or", "of") that could appear inside unrelated words.
_MIN_MATCH_LEN = 3


def _get_embed_client(mailbox: orm.Mailbox):
    """Return an EmbedClient for L2 retrieval, or None to skip L2.

    Returns None when VOYAGE_API_KEY is absent — L2 silently degrades to
    L1-only, preserving the S5 behavior for mailboxes without embeddings.
    Tests monkeypatch this function to inject FakeEmbedClient.
    """
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        return None
    try:
        from services.retrieval.embed_client import VoyageEmbedClient
        return VoyageEmbedClient(api_key=key)
    except Exception as exc:
        _log.warning(
            "L2 embed client construction failed (%s) — L2 disabled for this request",
            type(exc).__name__,
        )
        return None


def _run_l2(
    query: str,
    embed_client,
    mailbox_id: str,
    db: Session,
) -> list[RetrievalHit]:
    """Run L2 hybrid retrieval; return hits or [] on skip/failure.

    Returns [] immediately when embed_client is None (no VOYAGE_API_KEY set).
    Exceptions from hybrid_search are swallowed so L2 failure never breaks
    the endpoint — the caller falls back to L1-only evidence.
    All returned hits use message_id_header (RFC 5322), not internal UUID.
    """
    if embed_client is None:
        return []
    try:
        query_vec = embed_client.embed_query(query)
        result = hybrid_search(db, mailbox_id, query_vec, query, _CFM_RETRIEVAL_PARAMS)
        return result if isinstance(result, list) else []
    except Exception as exc:
        _log.warning(
            "L2 retrieval failed for mailbox %s (%s) — falling back to L1-only",
            mailbox_id, type(exc).__name__,
        )
        return []


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
    mbx = _get_mailbox(db, mailbox_id)
    matched_person, matched_project = _route(body.query, db, mailbox_id)

    # "Who do I ask about <project>?" → pure L1, no model call, no L2 retrieval.
    # Short-circuit before embed client construction to avoid unnecessary cost
    # and privacy exposure.
    if matched_project is not None and WHO_ASK.search(body.query):
        result, routed_to = synthesize_cover_for_me(
            body.query, None, matched_project, db=db, mailbox_id=mailbox_id, synth_fn=None
        )
        return CoverForMeResponse(query=body.query, routed_to=routed_to, result=result)

    # L2 runs alongside L1 when an embed client is available (spec Q5/S7.11).
    # Returns [] when VOYAGE_API_KEY is absent — L1-only path preserved (S5).
    # Returns [] with a logged warning when retrieval fails (key present but error).
    embed_client = _get_embed_client(mbx)
    l2_hits = _run_l2(body.query, embed_client, mailbox_id, db)

    # No L1 entity match.
    if matched_person is None and matched_project is None:
        if not l2_hits:
            # Both L1 and L2 empty → insufficient evidence; no model call.
            result, routed_to = synthesize_cover_for_me(
                body.query, None, None, db=db, mailbox_id=mailbox_id, synth_fn=None
            )
            return CoverForMeResponse(query=body.query, routed_to=routed_to, result=result)

        # L2 is the primary source — model call required.
        try:
            synth_fn = make_anthropic_synth_fn(PARAMS)
        except MissingApiKeyError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        result, routed_to = synthesize_cover_for_me(
            body.query, None, None,
            db=db, mailbox_id=mailbox_id, synth_fn=synth_fn,
            l2_hits=l2_hits,
        )
        return CoverForMeResponse(query=body.query, routed_to=routed_to, result=result)

    # L1 matched — primary synthesis with L2 headers added to the allow-list.
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
        l2_hits=l2_hits,
    )
    return CoverForMeResponse(query=body.query, routed_to=routed_to, result=result)
