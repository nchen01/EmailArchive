"""FTS retrieval via Postgres full-text search on subject_clean_tsv (S7.7).

Public surface:
    fts_search(session, mailbox_id, query, params) -> list[RetrievalHit]

Uses the subject_clean_tsv GENERATED column added in migration 0006, which
indexes both subject and clean_text under the 'english' text-search config.
websearch_to_tsquery handles AND/OR/phrase/negation without raising on any
well-formed or malformed user input.

Empty queries and stop-word-only queries are handled gracefully: empty strings
are short-circuited in Python; stop-word-only strings produce a NULL tsquery
from Postgres, so the @@ operator returns no rows.

Noise and sensitivity filters use the same defaults and SQL clauses as
vector_search (S7.6).  vector_score is always None on returned hits; the
hybrid merge (S7.8) fills it in for messages that also appear in vector
results.
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.db import models as orm
from services.ingest.normalize.mime import decode_mime_words
from services.retrieval.contracts import RetrievalHit
from services.retrieval.params import RetrievalParams

log = logging.getLogger(__name__)

_SNIPPET_LEN = 300


def fts_search(
    session: Session,
    mailbox_id: str | UUID,
    query: str,
    params: RetrievalParams,
) -> list[RetrievalHit]:
    """Return up to ``params.fts_top_k`` messages ordered by descending ts_rank.

    websearch_to_tsquery is evaluated once in a CTE to avoid repeating the
    expression in both the WHERE clause and the ts_rank() call.
    """
    mid = str(mailbox_id)

    if not query.strip():
        return []

    noise_clause = "" if params.include_noise else "AND m.noise = false"
    sens_clause  = "" if params.include_sensitive else "AND m.sensitivity = '{none}'"

    # tsquery is computed once in a CTE; NULL result (empty/stop-word query)
    # makes the @@ predicate return NULL → no rows, which is the correct
    # graceful behaviour for stop-word-only queries.
    sql = text(f"""
        WITH tsq AS (
            SELECT websearch_to_tsquery('english', :query) AS q
        )
        SELECT
            m.id::text                        AS message_id,
            m.message_id_header,
            m.thread_id::text                 AS thread_id,
            m.subject,
            m.clean_text,
            m.ts,
            m.sensitivity,
            m.noise,
            m.sender_email,
            m.to_emails,
            m.cc_emails,
            ts_rank(m.subject_clean_tsv, tsq.q) AS fts_score
        FROM message m, tsq
        WHERE m.mailbox_id = :mid
          AND m.subject_clean_tsv @@ tsq.q
          {noise_clause}
          {sens_clause}
        ORDER BY fts_score DESC, m.ts DESC, m.id
        LIMIT :k
    """)  # noqa: S608 — noise/sens clauses are static strings, not user input

    rows = session.execute(sql, {
        "mid":   mid,
        "query": query,
        "k":     params.fts_top_k,
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
        score = float(r["fts_score"])

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
                vector_score=None,
                fts_score=score,
                rerank_score=score,
                source="fts",
                sensitivity=tuple(r["sensitivity"] or ["none"]),
                noise=bool(r["noise"]),
            )
        )

    log.debug("fts_search: mailbox=%s hits=%d", mid, len(hits))
    return hits
