"""S7.10 — retrieval eval hard-gate tests.

Seeds a full L0+L1 mailbox from the standard fixture, embeds all eligible
messages with FakeEmbedClient (idempotent), then runs every EVAL_CASE through
hybrid_search and asserts every hard gate passes.

Soft targets (MRR >= 0.6, top-1 precision >= 0.5) are checked as warnings
rather than failures so a slightly degraded FakeEmbedClient run does not block
CI; they exist to catch large regressions.

Skipped when DATABASE_URL is not set or Postgres is unreachable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
ROOT = Path(__file__).resolve().parent.parent

OWNER_EMAIL      = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]


def _db_reachable() -> bool:
    if not DATABASE_URL:
        return False
    try:
        from services.db.engine import engine
        with engine.connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres unreachable (run `docker compose up -d`).",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def eval_mailbox():
    """Create an isolated L0+L1 mailbox, embed it with FakeEmbedClient, yield
    (session, mailbox_id).  Tears down the mailbox (cascades) on exit.

    scope=module: seeding is expensive; one mailbox serves all eval tests.
    """
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import persist_l0, persist_l1
    from services.enrich.clustering.eval.run_eval import EVAL_PARAMS as CLUSTER_PARAMS
    from services.enrich.clustering.testkit import FakeNlp, make_test_embed
    from services.enrich.events.eval.run_eval import make_gold_extract_fn
    from services.enrich.pipeline import run_enrichment
    from services.ingest.params import IngestParams
    from services.ingest.pipeline import IngestConfig, run_ingest
    from services.retrieval.embed_client import FakeEmbedClient
    from scripts.embed_backfill import run_backfill

    session = SessionLocal()

    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=OWNER_EMAIL,
        status="active",
        embed_model="fake-embed",
        embed_dim=1024,
        config={"internal_domains": INTERNAL_DOMAINS},
    )
    session.add(mbx)
    session.commit()
    mailbox_id = str(mbx.id)

    # L0 ingest
    ingest_params = IngestParams(
        legal_domains=["morrislaw.com"],
        hr_senders=["hr@acme.com"],
        personal_domains=[],
    )
    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=ROOT / "fixtures" / "mailbox.json",
        owner_email=OWNER_EMAIL,
        internal_domains=INTERNAL_DOMAINS,
        params=ingest_params,
    )
    store = run_ingest(cfg)
    persist_l0(store, mailbox_id, session)

    # L1 enrichment (project_ids, person_ids used for hybrid boost)
    gold = json.loads(
        (ROOT / "fixtures" / "gold" / "events.json").read_text(encoding="utf-8")
    )
    extract_fn = make_gold_extract_fn(store, gold)
    result = run_enrichment(
        store.messages, OWNER_EMAIL, INTERNAL_DOMAINS,
        threads=store.threads, embed_fn=make_test_embed(), nlp=FakeNlp(),
        cluster_params=CLUSTER_PARAMS, extract_fn=extract_fn,
    )
    persist_l1(result, mailbox_id, session)

    # Embed all eligible messages with FakeEmbedClient (idempotent)
    client = FakeEmbedClient(dim=1024, model="fake-embed")
    run_backfill(
        mailbox_id=mailbox_id,
        embed_client=client,
        batch_size=64,
        dry_run=False,
        confirm=True,
        session=session,
    )

    try:
        yield session, mailbox_id
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id))
        session.commit()
        session.close()


# ── Hard gate tests ───────────────────────────────────────────────────────────

def test_eval_all_hard_gates_pass(eval_mailbox):
    """Run every EVAL_CASE; assert every hard gate passes."""
    from services.retrieval.eval.run_eval import EvalFailure, run_eval

    session, mailbox_id = eval_mailbox
    try:
        result = run_eval(session, mailbox_id, verbose=True)
    except EvalFailure as exc:
        pytest.fail(str(exc))

    assert result.hard_gates_passed


def test_eval_no_sensitive_leakage(eval_mailbox):
    """hr-1 and legal-1 must never appear in any eval result."""
    from services.retrieval.eval.run_eval import run_eval

    session, mailbox_id = eval_mailbox
    result = run_eval(session, mailbox_id)

    forbidden = {"hr-1@acme.com", "legal-1@morrislaw.com"}  # no brackets; ingest strips them
    for cr in result.case_results:
        if not isinstance(cr.hits, list):
            continue
        returned = {h.message_id_header for h in cr.hits}
        leaked   = returned & forbidden
        assert not leaked, (
            f"Case {cr.case.query!r}: sensitive messages leaked: {leaked}"
        )


def test_eval_no_noise_in_results(eval_mailbox):
    """news-1 (noise=True) must never appear in any eval result."""
    from services.retrieval.eval.run_eval import run_eval

    session, mailbox_id = eval_mailbox
    result = run_eval(session, mailbox_id)

    for cr in result.case_results:
        if not isinstance(cr.hits, list):
            continue
        noise_hits = [h for h in cr.hits if h.noise]
        assert not noise_hits, (
            f"Case {cr.case.query!r}: noise messages appeared: "
            f"{[h.message_id_header for h in noise_hits]}"
        )


def test_eval_all_citations_in_db(eval_mailbox):
    """Every returned message_id_header must exist in the DB — citation contract."""
    from sqlalchemy import text
    from services.retrieval.eval.run_eval import run_eval

    session, mailbox_id = eval_mailbox
    result = run_eval(session, mailbox_id)

    db_headers = {
        r[0] for r in session.execute(
            text("SELECT message_id_header FROM message WHERE mailbox_id = :mid"),
            {"mid": mailbox_id},
        ).all()
    }
    for cr in result.case_results:
        if not isinstance(cr.hits, list):
            continue
        for hit in cr.hits:
            assert hit.message_id_header in db_headers, (
                f"Case {cr.case.query!r}: {hit.message_id_header!r} "
                f"not found in DB — invalid citation"
            )


def test_eval_unanswerable_returns_insufficient_evidence(eval_mailbox):
    """The xyzzy query must return InsufficientEvidence, not a list of hits."""
    from services.retrieval.contracts import InsufficientEvidence
    from services.retrieval.embed_client import FakeEmbedClient
    from services.retrieval.eval.fixtures import EVAL_PARAMS
    from services.retrieval.hybrid import hybrid_search

    session, mailbox_id = eval_mailbox
    client = FakeEmbedClient(dim=1024, model="fake-embed")
    query  = "xyzzy frobnicator spaghetti"

    result = hybrid_search(
        session, mailbox_id,
        client.embed_query(query), query,
        EVAL_PARAMS,
    )
    assert isinstance(result, InsufficientEvidence), (
        f"Expected InsufficientEvidence for unanswerable query, "
        f"got {len(result)} hits"  # type: ignore[arg-type]
    )


# ── Soft target tests (warn, not fail) ────────────────────────────────────────

MRR_TARGET  = 0.6
TOP1_TARGET = 0.5


def test_eval_mrr_soft_target(eval_mailbox):
    """Report MRR; emit a UserWarning (visible in pytest -W output) if below target.

    Never fails the test — soft targets are informational, not blocking.
    """
    import warnings
    from services.retrieval.eval.run_eval import run_eval

    session, mailbox_id = eval_mailbox
    result = run_eval(session, mailbox_id)

    print(f"\n  MRR = {result.mrr:.3f} (target >= {MRR_TARGET})")
    if result.mrr < MRR_TARGET:
        warnings.warn(
            f"MRR {result.mrr:.3f} below soft target {MRR_TARGET}",
            UserWarning,
            stacklevel=1,
        )


def test_eval_top1_precision_soft_target(eval_mailbox):
    """Report top-1 precision; emit a UserWarning if below target.

    Never fails the test — soft targets are informational, not blocking.
    """
    import warnings
    from services.retrieval.eval.run_eval import run_eval

    session, mailbox_id = eval_mailbox
    result = run_eval(session, mailbox_id)

    print(f"\n  Top-1 precision = {result.top1_precision:.3f} (target >= {TOP1_TARGET})")
    if result.top1_precision < TOP1_TARGET:
        warnings.warn(
            f"Top-1 precision {result.top1_precision:.3f} below soft target {TOP1_TARGET}",
            UserWarning,
            stacklevel=1,
        )
