"""`gmail_ingest_window` job handler (S25 — moves S16.0 date-range ingest onto the
S24 job runner).

Runs the SAME `run_windowed_ingest` the synchronous endpoint used to call, now as
a durable job: verify the connected account, then fetch/normalize/persist the
date-windowed snapshot (scoped snapshot — sync token bypassed and never saved;
`replace_snapshot` clears existing derived data first). S25 moves ONLY date-range
ingest — not enrichment / embedding backfill / project materialization (S26).

Safeguards preserved: the enqueuing endpoint still validates the window, requires
`confirm`, requires a window for `replace_snapshot`, and verifies the account
before enqueue (fail-fast 409). The handler re-verifies the account and honors
cancellation at checkpoints. All progress/summary/errors are safe metadata only.
"""
from __future__ import annotations

from typing import Callable

from services.jobs.registry import JobContext, JobError, JobResult, register

# Provider seam (tests inject a fake). Defaults to the D6/env Gmail provider — S25
# does not rewire ingest onto the S23 vault resolver; that stays a later step.
def _default_provider_factory(token_mailbox_id: str):
    from services.ingest.gmail_windowed import build_gmail_provider
    return build_gmail_provider(token_mailbox_id)


provider_factory: Callable[[str], object] = _default_provider_factory


def set_provider_factory(fn: Callable[[str], object]) -> None:
    global provider_factory
    provider_factory = fn


@register("gmail_ingest_window")
def run(ctx: JobContext) -> JobResult:
    from services.db import models as orm
    from services.ingest.gmail_windowed import (
        AccountMismatchError, run_windowed_ingest, verify_account,
    )
    from services.ingest.list_options import parse_date_window

    p = ctx.params
    mailbox_id = p.get("mailbox_id")
    db = ctx._db
    mbx = db.get(orm.Mailbox, mailbox_id) if mailbox_id else None
    if mbx is None:
        raise JobError("mailbox_not_found")

    options = parse_date_window(p.get("date_from"), p.get("date_to"))
    provider = provider_factory(mailbox_id)

    ctx.progress(phase="verifying")
    ctx.check_canceled()
    try:
        verify_account(provider, mbx.owner_email)
    except AccountMismatchError:
        raise JobError("account_mismatch") from None

    ctx.check_canceled()
    ctx.progress(phase="ingesting")
    domains = p.get("internal_domains")
    effective_domains = (
        list(domains) if domains is not None
        else list((mbx.config or {}).get("internal_domains", []))
    )
    summary = run_windowed_ingest(
        db, db_mailbox_id=mailbox_id, token_mailbox_id=mailbox_id,
        owner_email=mbx.owner_email, internal_domains=effective_domains,
        options=options, max_messages=int(p.get("max_messages", 500)),
        replace_snapshot=bool(p.get("replace_snapshot")), provider=provider,
    )

    # Persist request-supplied internal_domains only on success (mirrors S16.0).
    if domains is not None:
        mbx = db.get(orm.Mailbox, mailbox_id)
        cfg = dict(mbx.config or {})
        cfg["internal_domains"] = list(domains)
        mbx.config = cfg
        db.commit()

    return JobResult(
        summary=f"ingested {summary['messages']} messages",
        progress={
            "phase": "done",
            "messages": summary["messages"],
            "threads": summary["threads"],
            "replaced": bool(summary["replaced"]),
            "sync_token_disposition": summary["sync_token_disposition"],
        },
    )
