"""Run the clustering eval against the shared fixtures (spec 03 §18, §22).

Pipeline:
1. run_ingest (FixtureProvider) + run_enrichment -> real thread UUIDs / person_ids.
2. Gold dict {thread_uuid: set(project_labels)}; threads with empty gold excluded.
3. cluster_mailbox with the deterministic test embed_fn + fake NLP.
4. Pred dict {thread_uuid: set(project_ids)} restricted to gold threads.
5. Compute ext_bcubed / omega / pairwise_f1 and check the hard DoD gates.

Hard gates (decision C): BCubed F1 >= 0.75, precision >= 0.80, multi-gold >= 80%,
determinism (checked in the pipeline test). Omega is printed but NOT gated.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from services.enrich.clustering.params import ClusteringParams, FIXTURE_PARAMS
from services.enrich.clustering.pipeline import cluster_from_store
from services.enrich.clustering.testkit import FakeNlp, make_test_embed

from .metrics import ext_bcubed, omega_index, pairwise_f1

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "fixtures"

OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]

# Canonical fixture/eval params live in params.py so dev_seed and run_eval
# stay in sync. Import and alias for readability in this module.
EVAL_PARAMS = FIXTURE_PARAMS


def _load_inputs():
    from services.enrich.pipeline import run_enrichment
    from services.ingest.params import IngestParams
    from services.ingest.pipeline import IngestConfig, run_ingest

    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=FIXTURES / "mailbox.json",
        owner_email=OWNER_EMAIL,
        internal_domains=INTERNAL_DOMAINS,
        params=IngestParams(
            legal_domains=["morrislaw.com"], hr_senders=["hr@acme.com"], personal_domains=[]
        ),
    )
    store = run_ingest(cfg)
    enrich = run_enrichment(store.messages, OWNER_EMAIL, INTERNAL_DOMAINS)
    e2p = {i.email: i.person_id for i in enrich.identities if i.person_id}
    by_thread: dict = defaultdict(list)
    for m in store.messages:
        by_thread[m.thread_id].append(m)
    return store, e2p, by_thread


def _gold(store) -> dict:
    """Map thread UUID -> set(gold project labels), excluding empty-gold threads."""
    threads_gold = json.loads(
        (FIXTURES / "gold" / "threads.json").read_text(encoding="utf-8")
    )["members_by_provider_id"]
    projects = json.loads((FIXTURES / "gold" / "projects.json").read_text(encoding="utf-8"))
    pid2tid = {m.provider_id: m.thread_id for m in store.messages}
    gold: dict = {}
    for tlabel, provider_ids in threads_gold.items():
        labels = projects.get(tlabel, [])
        if not labels:
            continue
        tid = pid2tid[provider_ids[0]]
        gold[tid] = set(labels)
    return gold


def run(params: ClusteringParams = FIXTURE_PARAMS, verbose: bool = True) -> dict:
    store, e2p, by_thread = _load_inputs()
    gold = _gold(store)

    result = cluster_from_store(
        store.threads, by_thread, e2p, e2p[OWNER_EMAIL],
        embed_fn=make_test_embed(), nlp=FakeNlp(), params=params,
    )

    # Pred dict over gold threads only.
    pred_all: dict = defaultdict(set)
    for a in result.assignments:
        pred_all[a.thread_id].add(a.project_id)
    pred = {tid: (pred_all.get(tid) or {f"__singleton_{tid}"}) for tid in gold}

    bp, br, bf = ext_bcubed(gold, pred)
    om = omega_index(gold, pred)
    pp, pr, pf = pairwise_f1(gold, pred)

    multi_gold = [t for t, labs in gold.items() if len(labs) >= 2]
    multi_ok = sum(1 for t in multi_gold if len(pred[t]) >= 2)
    multi_rate = (multi_ok / len(multi_gold)) if multi_gold else 1.0

    gates = {
        "bcubed_f1>=0.75": bf >= 0.75,
        "bcubed_precision>=0.80": bp >= 0.80,
        "multi_gold>=0.80": multi_rate >= 0.80,
    }
    summary = dict(
        bcubed=dict(precision=bp, recall=br, f1=bf),
        omega=om,
        pairwise=dict(precision=pp, recall=pr, f1=pf),
        multi_gold_rate=round(multi_rate, 3),
        n_gold_threads=len(gold),
        n_projects=len(result.projects),
        gates=gates,
        passed=all(gates.values()),
    )

    if verbose:
        print(json.dumps(summary, indent=2))
        print("PASS" if summary["passed"] else "FAIL")
    return summary


if __name__ == "__main__":
    import sys

    s = run()
    sys.exit(0 if s["passed"] else 1)
