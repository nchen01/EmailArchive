"""L0 orchestrator (spec 00 §17): authorize → fetch → normalize → persist."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .list_options import ListOptions
from .normalize.threads import reconstruct
from .params import IngestParams
from .providers.base import MailProvider
from .providers.fixture import FixtureProvider
from .store import IngestStore, persist


@dataclass
class IngestConfig:
    provider: str = "fixture"            # "fixture" | "gmail" | "msgraph"
    mailbox_path: Path | None = None     # for fixture provider
    mailbox_id: str = ""                 # provider credential ID (gmail/msgraph OAuth)
    db_mailbox_id: str = ""              # DB mailbox UUID; scopes Message/Thread PKs so two
                                         # mailboxes can hold the same RFC Message-ID without
                                         # primary-key collision.  Empty = in-memory/test mode.
    owner_email: str = ""
    internal_domains: list[str] = field(default_factory=list)
    params: IngestParams = field(default_factory=IngestParams)
    # Incremental sync and fetch cap.  These are intentionally on IngestConfig
    # (not left to fetch_all()) so callers cannot accidentally ignore them.
    since_token: str | None = None       # Gmail historyId / Graph delta token; None = full fetch
    max_messages: int | None = None      # hard cap on messages fetched; None = no limit
    # Source-selection date window (S16.0).  A scoped snapshot: when set, the
    # stored/passed sync_token is ignored (see run_ingest).  None = no window.
    list_options: ListOptions | None = None


def make_provider(cfg: IngestConfig) -> MailProvider:
    if cfg.provider == "fixture":
        return FixtureProvider(cfg.mailbox_path)
    elif cfg.provider == "gmail":
        from .providers.gmail import GmailProvider

        return GmailProvider(cfg.params, cfg.mailbox_id)
    else:
        from .providers.msgraph import MSGraphProvider

        return MSGraphProvider()


def run_ingest(cfg: IngestConfig) -> IngestStore:
    """Fetch, normalize, and return an in-memory IngestStore.

    When ``since_token``, ``max_messages``, or a ``list_options`` date window is
    set, the fetch path uses ``provider.list_ids(since_token, options)`` directly
    and caps the ID list before issuing any ``fetch()`` calls — so the limits
    cannot be accidentally bypassed by a provider's ``fetch_all()`` implementation.

    A date window makes the run a **scoped snapshot**: any ``since_token`` is
    ignored so the window is not silently narrowed by incremental history
    (D-S16.0-5).
    """
    provider = make_provider(cfg)
    provider.authorize({})

    options = cfg.list_options
    windowed = options is not None and options.is_windowed()
    # Scoped snapshot: date-windowed runs never use the incremental sync token.
    since_token = None if windowed else cfg.since_token

    if since_token is not None or cfg.max_messages is not None or windowed:
        import itertools
        id_stream = provider.list_ids(since_token, options)
        # islice stops the generator at max_messages without ever enumerating
        # the full mailbox — the provider never paginates beyond the cap.
        if cfg.max_messages is not None:
            ids = list(itertools.islice(id_stream, cfg.max_messages))
        else:
            ids = list(id_stream)
        raws = [provider.fetch(id_) for id_ in ids]
    else:
        raws = list(provider.fetch_all())

    messages, threads = reconstruct(raws, cfg.owner_email, cfg.params, cfg.db_mailbox_id)
    return persist(messages, threads)
