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


class AccountMismatchError(ValueError):
    """The OAuth token's Gmail account does not match the target mailbox owner.

    Message is intentionally generic (no email addresses) so it is safe to
    surface to an operator/UI without leaking which account the token belongs to.
    """


def verify_account(provider, expected_owner_email: str) -> None:
    """Fail BEFORE any listing/fetch if the token's account != the mailbox owner.

    Guards a browser- or env-triggered ingest from using a Gmail token for one
    account and persisting that account's mail into a different mailbox row
    (protects both GMAIL_TOKEN_<id> and the fallback GMAIL_TOKEN). Compared
    case-insensitively. Providers without ``get_profile_email`` (e.g. test fakes)
    are skipped.
    """
    getter = getattr(provider, "get_profile_email", None)
    if getter is None:
        return
    actual = (getter() or "").strip().lower()
    expected = (expected_owner_email or "").strip().lower()
    if not actual:
        raise AccountMismatchError("could not verify the Gmail account for this token")
    if actual != expected:
        raise AccountMismatchError(
            "the Gmail token's account does not match this mailbox's owner; "
            "refusing to ingest to avoid mixing accounts"
        )


def build_gmail_provider(token_mailbox_id: str, params: IngestParams | None = None):
    """Construct + authorize a GmailProvider using the env-configured OAuth token.

    The token is read by ``get_token`` (``GMAIL_TOKEN_<id>`` or ``GMAIL_TOKEN``)
    and never logged or returned. Kept as a seam so tests can inject a fake
    provider instead. Used by the CLI smoke path.
    """
    from .providers.gmail import GmailProvider

    provider = GmailProvider(params or IngestParams(), token_mailbox_id)
    provider.authorize({})  # env token via get_token; never logged
    return provider


def authorized_gmail_provider(db, mailbox_id: str, params: IngestParams | None = None):
    """Construct + authorize a GmailProvider via the S23 credential resolver.

    Resolves the grant through ``resolve_gmail_grant``: the **vault-backed
    connected account** in production (a short-lived access token, never a stored
    token), or the D6 env token in ``AUTH_MODE=dev``. Raises ``ProviderNotConnected``
    when production has no connected account. The token/grant is never logged.
    """
    from services.oauth.resolver import resolve_gmail_grant

    from .providers.gmail import GmailProvider

    grant = resolve_gmail_grant(db, mailbox_id)  # vault (prod) or env (dev)
    provider = GmailProvider(params or IngestParams(), mailbox_id)
    provider.authorize(grant)
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
    replace_snapshot: bool = False,
    params: IngestParams | None = None,
    provider=None,
) -> dict:
    """Fetch a date-windowed snapshot and persist L0 + L1 (identity/graph/roles).

    Shares the CLI snapshot semantics: the sync token is bypassed
    (``list_ids(None, options)``) and **never saved**. Clustering + event
    extraction are deferred (no embedding model here), matching the smoke runner.

    ``replace_snapshot=True`` first fetches the new window, THEN clears all
    mailbox-scoped derived data (see ``clear_mailbox_snapshot_for_reingest``), so
    the resulting surfaces reflect only this window. The default (False) is a
    plain append/upsert.

    Transaction boundary (partial atomicity — an accepted limitation this sprint):
      - **Fetch-before-clear**: the window is fetched and normalized before any
        delete, so a Gmail failure never wipes an existing snapshot.
      - The clear runs with ``commit=False``, so it commits **atomically with the
        L0 write** — if ``persist_l0`` fails, the clear rolls back and the old
        snapshot is intact.
      - L1 (``persist_l1``) commits separately (it commits internally). If L1
        fails *after* L0 committed, the mailbox holds the new window's L0 with
        partial/absent L1 — a re-runnable state, not data loss. Full
        clear→L0→L1 atomicity would require persist_* to stop committing
        internally, which is out of scope here.

    Returns a summary dict (no token, no raw content).
    """
    from services.db import models as orm
    from services.db.store import (
        clear_mailbox_snapshot_for_reingest,
        persist_l0,
        persist_l1,
    )
    from services.enrich.params import EnrichParams
    from services.enrich.pipeline import run_enrichment

    from .normalize.threads import reconstruct
    from .store import persist as ingest_persist

    params = params or IngestParams()
    if provider is None:
        provider = build_gmail_provider(token_mailbox_id, params)

    # Fetch FIRST (scoped snapshot, token bypassed; N+1 to detect the cap). Doing
    # this before any destructive clear guarantees a Gmail failure cannot leave a
    # replace-mode mailbox wiped-but-empty.
    raw_ids = list(itertools.islice(provider.list_ids(None, options), max_messages + 1))
    hit_cap = len(raw_ids) > max_messages
    ids = raw_ids[:max_messages]
    raws = [provider.fetch(i) for i in ids]

    messages, threads = reconstruct(raws, owner_email, params, db_mailbox_id)
    store = ingest_persist(messages, threads)

    # Only now (new data safely in hand) clear the old snapshot, if requested.
    # commit=False → the clear commits atomically with persist_l0 below, so a
    # failed L0 write rolls the clear back and leaves the old snapshot intact.
    cleared: dict[str, int] | None = None
    if replace_snapshot:
        cleared = clear_mailbox_snapshot_for_reingest(session, db_mailbox_id, commit=False)

    persist_l0(store, db_mailbox_id, session, replace_snapshot=False)
    result = run_enrichment(
        store.messages,
        owner_email=owner_email,
        internal_domains=internal_domains,
        params=EnrichParams(),
        # threads intentionally omitted → clustering + event extraction skipped
    )
    persist_l1(result, db_mailbox_id, session)

    # Mirror the CLI: set the mailbox owner_person_id from the owner identity if
    # it is not already set, so downstream surfaces resolve the owner.
    owner_pid = next(
        (i.person_id for i in result.identities if i.email == owner_email.lower()), None
    )
    if owner_pid:
        mbx = session.get(orm.Mailbox, db_mailbox_id)
        if mbx and not mbx.owner_person_id:
            mbx.owner_person_id = owner_pid
            session.commit()

    return {
        "messages": len(store.messages),
        "threads": len(store.threads),
        "people": len(result.people),
        "edges": len(result.edges),
        "hit_cap": hit_cap,
        "replaced": replace_snapshot,
        "cleared": cleared,
        # Windowed run is a scoped snapshot — a sync token is never saved.
        "sync_token_disposition": "not_saved (date-windowed snapshot)",
        "persisted": True,
    }
