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


# ── Clustering helpers (spec 03) ─────────────────────────────────────────────

OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]


def build_cluster_inputs():
    """Run L0 + L1 and return everything build_thread_features needs.

    Returns ``(store, result, ctx)`` where ``ctx`` is a dict with keys
    ``threads``, ``messages_by_thread``, ``email_to_person_id``,
    ``owner_person_id``.
    """
    from services.enrich.pipeline import run_enrichment

    store = run_full_ingest()
    result = run_enrichment(store.messages, OWNER_EMAIL, INTERNAL_DOMAINS)
    e2p = {i.email: i.person_id for i in result.identities if i.person_id is not None}

    by_thread: dict[str, list] = {}
    for m in store.messages:
        by_thread.setdefault(m.thread_id, []).append(m)

    ctx = {
        "threads": store.threads,
        "messages_by_thread": by_thread,
        "email_to_person_id": e2p,
        "owner_person_id": e2p[OWNER_EMAIL],
    }
    return store, result, ctx


def build_features(dim: int = 16):
    """Build ThreadFeatures from the fixture with test embed + fake NLP."""
    from services.enrich.clustering.features import build_thread_features
    from services.enrich.clustering.labeling_tfidf import ThreadTfidf
    from services.enrich.clustering.testkit import FakeNlp, make_test_embed

    store, result, ctx = build_cluster_inputs()
    tfidf = ThreadTfidf.fit(ctx["threads"], ctx["messages_by_thread"])
    feats = build_thread_features(
        ctx["threads"],
        ctx["messages_by_thread"],
        ctx["email_to_person_id"],
        ctx["owner_person_id"],
        embed_fn=make_test_embed(dim),
        nlp=FakeNlp(),
        tfidf=tfidf,
    )
    return store, result, ctx, feats
