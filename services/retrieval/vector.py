"""Vector retrieval via pgvector HNSW cosine search (S7.6, D12b).

Public surface:
    vector_search(session, mailbox_id, query_embedding, params) -> list[RetrievalHit]

Returns hits sorted by descending cosine similarity (vector_score).  Noise and
sensitivity filters are applied in SQL.  project_ids and person_ids are resolved
via ORM queries on thread_project_assignment and identity.

The quality-score gate (min_vector_score) lives in the hybrid merge (S7.8), not
here — vector_search returns all results the DB returns within the LIMIT.
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.db import models as orm
from services.ingest.normalize.mime import decode_mime_words
from services.retrieval.contracts import RetrievalHit
from services.retrieval.embed_client import EmbedError
from services.retrieval.params import RetrievalParams

log = logging.getLogger(__name__)

_SNIPPET_LEN = 300


def vector_search(
    session: Session,
    mailbox_id: str | UUID,
    query_embedding: list[float],
    params: RetrievalParams,
) -> list[RetrievalHit]:
    """Return up to ``params.vector_top_k`` messages ordered by cosine similarity.

    Uses the HNSW index on message_embedding.embedding (migration 0006).
    The vector is passed as a Postgres literal cast to avoid psycopg2
    array-adapter ambiguity with the pgvector type.
    """
    mid = str(mailbox_id)

    if len(query_embedding) != params.embed_dim:
        raise EmbedError(
            f"query_embedding has {len(query_embedding)} dimensions, "
            f"expected embed_dim={params.embed_dim}"
        )

    # Format the query vector as a Postgres array literal for the CAST.
    # The values come from our own embed_client, never from user input.
    qvec_lit = "[" + ",".join(str(v) for v in query_embedding) + "]"

    noise_clause = "" if params.include_noise else "AND m.noise = false"
    sens_clause  = "" if params.include_sensitive else "AND m.sensitivity = '{none}'"

    sql = text(f"""
        SELECT
            m.id::text                                               AS message_id,
            m.message_id_header,
            m.thread_id::text                                        AS thread_id,
            m.subject,
            m.clean_text,
            m.ts,
            m.sensitivity,
            m.noise,
            m.sender_email,
            m.to_emails,
            m.cc_emails,
            1 - (me.embedding <=> CAST(:qvec AS vector))             AS vector_score
        FROM message_embedding me
        JOIN message m ON m.id = me.message_id
        WHERE me.mailbox_id = :mid
          AND me.embed_model = :model
          {noise_clause}
          {sens_clause}
        ORDER BY me.embedding <=> CAST(:qvec AS vector), m.ts DESC, m.id
        LIMIT :k
    """)  # noqa: S608 — noise_clause/sens_clause are static strings, not user input

    rows = session.execute(sql, {
        "mid":   mid,
        "model": params.embed_model,
        "qvec":  qvec_lit,
        "k":     params.vector_top_k,
    }).mappings().all()

    if not rows:
        return []

    # ── Resolve project_ids for all returned threads ───────────────────────────
    thread_ids = list({r["thread_id"] for r in rows})
    proj_rows = session.execute(
        select(
            orm.ThreadProjectAssignment.thread_id,
            orm.ThreadProjectAssignment.project_id,
        ).where(orm.ThreadProjectAssignment.thread_id.in_(thread_ids))
    ).all()
    proj_map: dict[str, list[str]] = {}
    for tid, pid in proj_rows:
        proj_map.setdefault(str(tid), []).append(str(pid))

    # ── Resolve person_ids for all emails in returned messages ────────────────
    all_emails: set[str] = set()
    for r in rows:
        all_emails.add(r["sender_email"])
        all_emails.update(r["to_emails"] or [])
        all_emails.update(r["cc_emails"] or [])

    email_to_person: dict[str, str] = {}
    if all_emails:
        id_rows = session.execute(
            select(orm.Identity.email, orm.Identity.person_id).where(
                orm.Identity.mailbox_id == mid,
                orm.Identity.email.in_(list(all_emails)),
                orm.Identity.person_id.isnot(None),
            )
        ).all()
        for email, person_id in id_rows:
            if person_id is not None:
                email_to_person[email] = str(person_id)

    # ── Build RetrievalHit objects ─────────────────────────────────────────────
    hits: list[RetrievalHit] = []
    for r in rows:
        msg_emails = (
            [r["sender_email"]]
            + list(r["to_emails"] or [])
            + list(r["cc_emails"] or [])
        )
        person_ids = tuple(
            sorted({email_to_person[e] for e in msg_emails if e in email_to_person})
        )
        project_ids = tuple(sorted(proj_map.get(r["thread_id"], [])))
        score = float(r["vector_score"])

        hits.append(
            RetrievalHit(
                message_id=r["message_id"],
                message_id_header=r["message_id_header"],
                thread_id=r["thread_id"],
                project_ids=project_ids,
                person_ids=person_ids,
                ts=r["ts"],
                subject=decode_mime_words(r["subject"] or ""),
                snippet=(r["clean_text"] or "")[:_SNIPPET_LEN],
                vector_score=score,
                fts_score=None,
                rerank_score=score,
                source="vector",
                sensitivity=tuple(r["sensitivity"] or ["none"]),
                noise=bool(r["noise"]),
            )
        )

    hits.sort(key=lambda h: h.vector_score or 0.0, reverse=True)
    log.debug(
        "vector_search: mailbox=%s model=%s hits=%d",
        mid, params.embed_model, len(hits),
    )
    return hits
