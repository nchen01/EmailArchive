"""Safe source-message detail endpoint (S14.1).

  GET /api/source-message/{mailbox_id}?message_id_header=<url-encoded>

Given a mailbox and an RFC 5322 ``Message-ID`` (the citation key used across the
app), return citation-safe metadata about that one message: subject, date,
sender display + domain, provider type, a bounded snippet, and a best-effort
Gmail search link. This is what powers the "inspect this citation" drawer.

Safety rules (AGENTS.md invariants):
  - Keyed on ``message_id_header``, never the internal ``Message.id``.
  - Mailbox boundary enforced in the WHERE clause.
  - Whole-thread sensitivity exclusion (like S9/S13): if the message OR any
    sibling in its thread carries a non-``{none}`` sensitivity tag, the endpoint
    returns 404 — indistinguishable from "does not exist", so there is no
    existence oracle for sensitive mail.
  - Snippet only (first 200 chars of ``clean_text``); never raw MIME, full body,
    tokens, or provider internals.
  - 404 for a missing header, a header in a different mailbox, or an excluded
    header. All three look identical to the caller by design.

Noise messages ARE returnable — a newsletter can be a legitimate citation; only
sensitivity excludes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from services.db import models as orm
from services.ingest.normalize.mime import decode_mime_words

from ..deps import get_db
from ..evidence import fetch_safe_source_rows, gmail_search_url, sender_fields
from ..schemas.evidence import SourceMessageDetail

router = APIRouter(tags=["source-message"])

_SNIPPET_CHARS = 200


@router.get("/source-message/{mailbox_id}", response_model=SourceMessageDetail)
async def source_message_endpoint(
    mailbox_id: str,
    message_id_header: str = Query(..., min_length=1, max_length=998),
    db: Session = Depends(get_db),
) -> SourceMessageDetail:
    mbx = db.get(orm.Mailbox, mailbox_id)
    if mbx is None:
        raise HTTPException(status_code=404, detail="mailbox not found")

    # Shared whole-thread sensitivity gate (same predicate as supporting_evidence).
    row = fetch_safe_source_rows(db, mailbox_id, [message_id_header]).get(
        message_id_header
    )
    if row is None:
        # Missing, wrong-mailbox, or sensitivity-excluded — indistinguishable.
        raise HTTPException(status_code=404, detail="source message not available")

    display, domain = sender_fields(row["sender_email"], row["addresses"])
    ts = row["ts"]
    return SourceMessageDetail(
        message_id_header=row["message_id_header"],
        subject=decode_mime_words(row["subject"] or ""),
        date=ts.isoformat() if ts else "",
        sender_display=display,
        sender_domain=domain,
        provider_type=mbx.provider,
        snippet=(row["clean_text"] or "")[:_SNIPPET_CHARS],
        source_type=None,
        open_url=gmail_search_url(mbx.provider, row["message_id_header"]),
    )
