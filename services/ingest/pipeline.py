"""L0 orchestrator (spec 00 §17): authorize → fetch → normalize → persist."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
    provider = make_provider(cfg)
    provider.authorize({})
    raws = list(provider.fetch_all())
    messages, threads = reconstruct(raws, cfg.owner_email, cfg.params, cfg.db_mailbox_id)
    return persist(messages, threads)
