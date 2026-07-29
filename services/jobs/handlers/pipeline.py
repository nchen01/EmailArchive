"""Post-ingest pipeline job handlers (S26 — implements S21 §4 job types).

Moves the remaining manual post-ingest work onto the S24 job runner as explicit,
tenant/mailbox-scoped, safe-metadata-only jobs, in dependency order:

  l1_enrichment         → identity / relationship graph / roles (run_enrichment
                          + persist_l1), re-run from stored L0.
  event_extraction      → grounded Event rows via the Anthropic extract_fn
                          (citation-enforced, sensitivity-gated). Needs an
                          ANTHROPIC key; never puts prompts/responses in metadata.
  embedding_backfill    → Voyage voyage-4 backfill (scripts.embed_backfill.run_backfill).
                          COST-GATED: the endpoint requires explicit confirm, and
                          the handler refuses unless params["cost_confirmed"]; no
                          live Voyage call happens without that operator confirm.
  project_materialization → clustering/materialize (scripts.materialize_projects),
                          S9 safeguards preserved.

Each handler reuses the EXISTING core functions (so the CLI scripts stay valid and
there is no duplicate logic), reports safe counts only, is idempotent, honors
cancellation, and maps failures to safe categories. Recipients never touch jobs.
"""
from __future__ import annotations

from typing import Callable

from services.jobs.registry import JobContext, JobError, JobResult, register


# ── seams (default = real core logic; tests inject fakes) ─────────────────────

def enrich_mailbox(db, mailbox_id: str) -> dict:
    """L1 enrichment (identity/graph/roles) from stored L0 → persist_l1. Local +
    deterministic; preserves sensitivity/noise (carried on the message rows) and
    strips the owner exactly once inside run_enrichment. Idempotent upsert."""
    from sqlalchemy import select

    from services.db import mappers, models as orm
    from services.db.store import persist_l1
    from services.enrich.pipeline import run_enrichment

    mbx = db.get(orm.Mailbox, mailbox_id)
    if mbx is None:
        raise JobError("mailbox_not_found")
    rows = db.execute(select(orm.Message).where(orm.Message.mailbox_id == mailbox_id)).scalars().all()
    messages = [mappers.row_to_message(r) for r in rows]
    internal = list((mbx.config or {}).get("internal_domains", []))
    result = run_enrichment(messages, mbx.owner_email, internal)  # no threads → no clustering/events
    persist_l1(result, mailbox_id, db)
    return {"messages": len(messages), "people": len(result.people),
            "identities": len(result.identities), "edges": len(result.edges)}


def extract_events_for_mailbox(db, mailbox_id: str, *, extract_fn=None) -> dict:
    """Grounded event extraction. `extract_fn` is injected (Anthropic in prod, a
    fake in tests) — this path never itself calls a network API. Requires L1
    (owner person) to have run first."""
    from sqlalchemy import select

    from services.db import mappers, models as orm
    from services.db.store import persist_events
    from services.enrich.events import extract_events

    mbx = db.get(orm.Mailbox, mailbox_id)
    if mbx is None:
        raise JobError("mailbox_not_found")
    if not mbx.owner_person_id:
        raise JobError("l1_enrichment_required")  # owner person is set by l1_enrichment

    if extract_fn is None:
        from services.enrich.events_llm import get_api_key, make_anthropic_extract_fn
        try:
            get_api_key()
        except RuntimeError:
            raise JobError("anthropic_key_missing") from None
        extract_fn = make_anthropic_extract_fn()

    thread_rows = db.execute(select(orm.Thread).where(orm.Thread.mailbox_id == mailbox_id)).scalars().all()
    threads = [mappers.row_to_thread(t) for t in thread_rows]
    msg_rows = db.execute(select(orm.Message).where(orm.Message.mailbox_id == mailbox_id)).scalars().all()
    messages = [mappers.row_to_message(m) for m in msg_rows]
    by_thread: dict[str, list] = {}
    for m in messages:
        by_thread.setdefault(m.thread_id, []).append(m)
    id_rows = db.execute(select(orm.Identity).where(orm.Identity.mailbox_id == mailbox_id)).scalars().all()
    e2p = {i.email: str(i.person_id) for i in id_rows if i.person_id}

    events = extract_events(
        threads, by_thread, assignments=[], email_to_person_id=e2p,
        owner_person_id=str(mbx.owner_person_id), extract_fn=extract_fn,
    )
    # Full re-extraction: processed scope = every message header (idempotent).
    processed = [m.message_id_header for m in messages]
    persist_events(events, mailbox_id, db, processed_headers=processed)
    return {"threads": len(threads), "events": len(events)}


# Embedding client seam — tests inject a FakeEmbedClient; a live Voyage client is
# only ever constructed under an explicit operator cost-confirm (never in tests).
_embed_client_factory: Callable[[str], object] | None = None


def set_embed_client_factory(fn: Callable[[str], object] | None) -> None:
    global _embed_client_factory
    _embed_client_factory = fn


def _make_embed_client(model: str):
    if _embed_client_factory is not None:
        return _embed_client_factory(model)
    # Live Voyage client — only reached under an explicit operator cost-confirm.
    import os

    from services.retrieval.embed_client import VoyageEmbedClient
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        raise JobError("voyage_key_missing")
    return VoyageEmbedClient(api_key=key, model=model)


def run_embedding_backfill(db, mailbox_id: str, *, batch_size: int = 64,
                           batch_delay_seconds: float = 0.0, embed_client=None) -> dict:
    """Voyage backfill via the existing run_backfill (confirm=True — the endpoint
    already gated the cost). Idempotent skip of already-embedded messages."""
    from scripts.embed_backfill import run_backfill

    client = embed_client or _make_embed_client("voyage-4")
    stats = run_backfill(
        mailbox_id=mailbox_id, embed_client=client, batch_size=batch_size,
        batch_delay_seconds=batch_delay_seconds, dry_run=False, confirm=True, session=db,
    )
    # Return only safe counts (drop nothing sensitive — these are already counts).
    return {k: stats[k] for k in ("total_messages", "already_embedded", "to_embed", "embedded", "model")
            if k in stats}


def run_project_materialization(db, mailbox_id: str, *, limit_threads: int | None = None) -> dict:
    """Project clustering/materialize via the existing S9 materialize (dry_run=False;
    requires embeddings, whole-thread sensitive exclusion, deterministic, idempotent)."""
    from scripts.materialize_projects import materialize

    stats = materialize(mailbox_id=mailbox_id, limit_threads=limit_threads, dry_run=False)
    return {k: stats[k] for k in (
        "total_threads", "eligible_threads", "excluded_threads", "projects_found",
        "projects_written", "assignments_written", "members_written") if k in stats}


# ── handlers ──────────────────────────────────────────────────────────────────

@register("l1_enrichment")
def _l1(ctx: JobContext) -> JobResult:
    ctx.progress(phase="enriching")
    ctx.check_canceled()
    stats = enrich_mailbox(ctx._db, ctx.params.get("mailbox_id"))
    return JobResult(summary=f"enriched {stats.get('messages', 0)} messages",
                     progress={"phase": "done", **stats})


@register("event_extraction")
def _events(ctx: JobContext) -> JobResult:
    ctx.progress(phase="extracting")
    ctx.check_canceled()
    stats = extract_events_for_mailbox(ctx._db, ctx.params.get("mailbox_id"))
    return JobResult(summary=f"extracted {stats.get('events', 0)} events",
                     progress={"phase": "done", **stats})


@register("embedding_backfill")
def _embed(ctx: JobContext) -> JobResult:
    if not ctx.params.get("cost_confirmed"):
        raise JobError("cost_not_confirmed")  # defense in depth behind the endpoint gate
    ctx.progress(phase="embedding")
    ctx.check_canceled()
    stats = run_embedding_backfill(
        ctx._db, ctx.params.get("mailbox_id"),
        batch_size=int(ctx.params.get("batch_size", 64)),
        batch_delay_seconds=float(ctx.params.get("batch_delay_seconds", 0.0)),
    )
    return JobResult(summary=f"embedded {stats.get('embedded', 0)} messages",
                     progress={"phase": "done", **stats})


@register("project_materialization")
def _materialize(ctx: JobContext) -> JobResult:
    ctx.progress(phase="materializing")
    ctx.check_canceled()
    stats = run_project_materialization(
        ctx._db, ctx.params.get("mailbox_id"), limit_threads=ctx.params.get("limit_threads"))
    return JobResult(summary=f"materialized {stats.get('projects_written', 0)} projects",
                     progress={"phase": "done", **stats})
