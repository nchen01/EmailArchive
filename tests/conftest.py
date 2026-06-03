import json
import sys
from pathlib import Path

# Make `packages` and `services` importable when running pytest from repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_DIR = ROOT / "fixtures"


def load_mailbox():
    return json.loads((FIXTURE_DIR / "mailbox.json").read_text(encoding="utf-8"))


def load_gold(name: str):
    return json.loads((FIXTURE_DIR / "gold" / f"{name}.json").read_text(encoding="utf-8"))


def mailbox_path() -> Path:
    return FIXTURE_DIR / "mailbox.json"


def run_full_ingest():
    """Run the L0 pipeline with the fixture's test sensitivity config."""
    from services.ingest.params import IngestParams
    from services.ingest.pipeline import IngestConfig, run_ingest

    params = IngestParams(
        legal_domains=["morrislaw.com"],
        hr_senders=["hr@acme.com"],
        personal_domains=[],
    )
    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=mailbox_path(),
        owner_email="alex@acme.com",
        internal_domains=["acme.com"],
        params=params,
    )
    return run_ingest(cfg)


def by_provider_id(store):
    return {m.provider_id: m for m in store.messages}
