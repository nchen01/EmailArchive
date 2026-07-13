"""Shared date-windowed Gmail plan/ingest helpers (S16.0).

Used by BOTH the CLI (`scripts/gmail_smoke_ingest.py`) and the demo-side backend
endpoint (`services/api/routers/gmail_ingest.py`) so the two cannot drift on the
core snapshot semantics:

- date validation lives in `list_options.parse_date_window`;
- the Gmail `q=` translation (inclusive `date_to`) lives in
  `providers.gmail.build_gmail_query`;
- a date window is a **scoped snapshot**: the stored/incremental sync token is
  bypassed (``since_token=None``) and a new one is never saved.

Neither function logs OAuth tokens or raw message content.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from .list_options import ListOptions
from .params import IngestParams


@dataclass(frozen=True)
class PlanResult:
    """Result of a preview: how many messages match, and whether it was capped."""
    count: int           # min(matches, max_messages)
    is_estimate: bool    # True when the true match count exceeds max_messages
    hit_cap: bool        # alias of is_estimate; the preview stopped at the cap


def plan_window(provider, options: ListOptions, max_messages: int) -> PlanResult:
    """List (not fetch) matching message IDs up to ``max_messages + 1``.

    Never fetches a raw body, never persists. Enumerates IDs (metadata only) so
    the count is exact when under the cap and a lower bound when capped.
    """
    ids = list(itertools.islice(provider.list_ids(None, options), max_messages + 1))
    hit_cap = len(ids) > max_messages
    return PlanResult(count=min(len(ids), max_messages), is_estimate=hit_cap, hit_cap=hit_cap)


def build_gmail_provider(token_mailbox_id: str, params: IngestParams | None = None):
    """Construct + authorize a GmailProvider using the env-configured OAuth token.

    The token is read by ``get_token`` (``GMAIL_TOKEN_<id>`` or ``GMAIL_TOKEN``)
    and never logged or returned. Kept as a seam so tests can inject a fake
    provider instead.
    """
    from .providers.gmail import GmailProvider

    provider = GmailProvider(params or IngestParams(), token_mailbox_id)
    provider.authorize({})  # env token via get_token; never logged
    return provider


def run_windowed_ingest(
    session,
    *,
    db_mailbox_id: str,
    token_mailbox_id: str,
    owner_email: str,
    internal_domains: list[str],
    options: ListOptions,
    max_messages: int,
    params: IngestParams | None = None,
    provider=None,
) -> dict:
    """Fetch a date-windowed snapshot and persist L0 + L1 (identity/graph/roles).

    Shares the CLI snapshot semantics: the sync token is bypassed
    (``list_ids(None, options)``) and **never saved**. Clustering + event
    extraction are deferred (no embedding model here), matching the smoke runner.
    Returns a summary dict (no token, no raw content).
    """
    from services.db.store import persist_l0, persist_l1
    from services.enrich.params import EnrichParams
    from services.enrich.pipeline import run_enrichment

    from .normalize.threads import reconstruct
    from .store import persist as ingest_persist

    params = params or IngestParams()
    if provider is None:
        provider = build_gmail_provider(token_mailbox_id, params)

    # Scoped snapshot: since_token=None; N+1 to detect the cap, then cap.
    raw_ids = list(itertools.islice(provider.list_ids(None, options), max_messages + 1))
    hit_cap = len(raw_ids) > max_messages
    ids = raw_ids[:max_messages]
    raws = [provider.fetch(i) for i in ids]

    messages, threads = reconstruct(raws, owner_email, params, db_mailbox_id)
    store = ingest_persist(messages, threads)

    persist_l0(store, db_mailbox_id, session, replace_snapshot=False)
    result = run_enrichment(
        store.messages,
        owner_email=owner_email,
        internal_domains=internal_domains,
        params=EnrichParams(),
        # threads intentionally omitted → clustering + event extraction skipped
    )
    persist_l1(result, db_mailbox_id, session)

    return {
        "messages": len(store.messages),
        "threads": len(store.threads),
        "people": len(result.people),
        "edges": len(result.edges),
        "hit_cap": hit_cap,
        # Windowed run is a scoped snapshot — a sync token is never saved.
        "sync_token_disposition": "not_saved (date-windowed snapshot)",
        "persisted": True,
    }
