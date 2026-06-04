"""Ticket 3.11 — pipeline.py end-to-end + determinism + sparse fallback (spec 03 §15)."""
from __future__ import annotations

import json

from conftest import build_cluster_inputs

from ekc_schemas import ClusteringResult
from services.enrich.clustering.params import ClusteringParams
from services.enrich.clustering.pipeline import cluster_from_store
from services.enrich.clustering.testkit import FakeNlp, make_test_embed

PARAMS = ClusteringParams(
    tau=0.12, w_part=0.30, w_emb=0.10, w_kw=0.45, w_temp=0.10, w_attach=0.05,
    ratio=0.2, min_aff=0.10,
)


def _run(params=PARAMS, prev=None):
    store, _, ctx = build_cluster_inputs()
    return store, cluster_from_store(
        ctx["threads"], ctx["messages_by_thread"], ctx["email_to_person_id"],
        ctx["owner_person_id"], embed_fn=make_test_embed(), nlp=FakeNlp(),
        params=params, prev_result=prev,
    )


def test_produces_valid_result():
    _, r = _run()
    assert isinstance(r, ClusteringResult)
    assert len(r.projects) >= 1
    assert r.run_meta["strategy"] == "agglomerative"  # 13 threads < 30


def test_invariants_every_thread_assigned():
    store, r = _run()
    all_tids = {t.id for t in store.threads if any(m.thread_id == t.id for m in store.messages)}
    assigned = {a.thread_id for a in r.assignments}
    assert assigned == all_tids


def test_byte_identical_determinism():
    _, r1 = _run()
    _, r2 = _run()
    j1 = r1.model_dump_json()
    j2 = r2.model_dump_json()
    assert j1 == j2  # byte-identical (DoD §22)


def test_run_meta_fields():
    _, r = _run()
    meta = r.run_meta
    for k in ("seed", "params_hash", "modularity", "n_communities", "orphan_ratio", "strategy"):
        assert k in meta


def test_sparse_fallback_path():
    """DoD §22: sparse-mailbox fallback (< min_threads_leiden) exercised."""
    _, r = _run()
    assert r.run_meta["strategy"] == "agglomerative"


def test_leiden_path_when_dense():
    """Force the Leiden path by lowering the threshold below the thread count."""
    p = ClusteringParams(
        tau=0.12, w_part=0.30, w_emb=0.10, w_kw=0.45, w_temp=0.10, w_attach=0.05,
        ratio=0.2, min_aff=0.10, min_threads_leiden=2,
    )
    _, r = _run(params=p)
    assert r.run_meta["strategy"] == "leiden"
    assert len(r.projects) >= 1


def test_id_carry_over_stable():
    _, first = _run()
    _, second = _run(prev=first)
    first_ids = {p.id for p in first.projects}
    second_ids = {p.id for p in second.projects}
    # Same input re-clustered with prev -> ids fully carried over.
    assert second_ids == first_ids


def test_assignment_ids_reference_projects():
    _, r = _run()
    pids = {p.id for p in r.projects}
    assert {a.project_id for a in r.assignments} <= pids


def test_debug_artifacts_emitted(tmp_path):
    """DoD §22: --debug must emit cluster_cards.json and similarity_graph.graphml."""
    from services.enrich.clustering.blocking import participant_idf
    from services.enrich.clustering.features import build_thread_features
    from services.enrich.clustering.labeling_tfidf import ThreadTfidf as CorpusTfidf
    from services.enrich.clustering.pipeline import write_debug_artifacts

    store, _, ctx = build_cluster_inputs()
    _, r = _run()

    tfidf = CorpusTfidf.fit(ctx["threads"], ctx["messages_by_thread"])
    feats = build_thread_features(
        ctx["threads"], ctx["messages_by_thread"],
        ctx["email_to_person_id"], ctx["owner_person_id"],
        embed_fn=make_test_embed(), nlp=FakeNlp(), tfidf=tfidf,
    )
    pidf, _, _ = participant_idf(feats, PARAMS.drop_frac)
    write_debug_artifacts(feats, r, pidf, out_dir=tmp_path, params=PARAMS)

    cards_file = tmp_path / "cluster_cards.json"
    graphml_file = tmp_path / "similarity_graph.graphml"

    assert cards_file.exists(), "cluster_cards.json not emitted"
    assert graphml_file.exists(), "similarity_graph.graphml not emitted"
    assert cards_file.stat().st_size > 0, "cluster_cards.json is empty"
    assert graphml_file.stat().st_size > 0, "similarity_graph.graphml is empty"

    cards = json.loads(cards_file.read_text())
    assert isinstance(cards, list) and len(cards) >= 1
    assert all("id" in c and "confidence" in c and "label" in c for c in cards)

    graphml = graphml_file.read_text()
    assert "<graphml" in graphml and "<graph" in graphml
