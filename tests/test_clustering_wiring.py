"""Wire clustering into the L1 pipeline (Step 7 / spec 03 §15)."""
from __future__ import annotations

from conftest import run_full_ingest

from ekc_schemas import ClusteringResult
from services.enrich.clustering.eval.run_eval import EVAL_PARAMS
from services.enrich.clustering.testkit import FakeNlp, make_test_embed
from services.enrich.pipeline import run_enrichment

OWNER = "alex@acme.com"
DOMAINS = ["acme.com"]


def test_clustering_none_without_threads():
    store = run_full_ingest()
    res = run_enrichment(store.messages, OWNER, DOMAINS)
    assert res.clustering is None


def test_clustering_runs_with_threads_and_fakes():
    store = run_full_ingest()
    res = run_enrichment(
        store.messages, OWNER, DOMAINS,
        threads=store.threads, embed_fn=make_test_embed(), nlp=FakeNlp(),
        cluster_params=EVAL_PARAMS,
    )
    assert isinstance(res.clustering, ClusteringResult)
    assert len(res.clustering.projects) >= 1
    # Edges/people still produced normally.
    assert res.edges and res.people


def test_clustering_deterministic_via_pipeline():
    store = run_full_ingest()

    def run():
        return run_enrichment(
            store.messages, OWNER, DOMAINS,
            threads=store.threads, embed_fn=make_test_embed(), nlp=FakeNlp(),
            cluster_params=EVAL_PARAMS,
        ).clustering

    assert run().model_dump_json() == run().model_dump_json()
