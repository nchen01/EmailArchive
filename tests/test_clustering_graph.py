"""Ticket 3.4 — similarity graph (spec 03 §8)."""
from __future__ import annotations

from conftest import build_features

from services.enrich.clustering.blocking import candidate_pairs, participant_idf
from services.enrich.clustering.graph import build_edges
from services.enrich.clustering.params import ClusteringParams

# Fixture-scale params: with deterministic test embeddings (no real semantics),
# similarity magnitudes are lower than the production default tuned for 768-dim
# sentence embeddings, so use a fixture-appropriate threshold here.
PARAMS = ClusteringParams(tau=0.12)


def _edges(params=PARAMS):
    _, _, _, feats = build_features()
    idf, ubiquitous, df = participant_idf(feats, params.drop_frac)
    pairs = candidate_pairs(feats, ubiquitous, df, params)
    return feats, build_edges(feats, pairs, idf, params)


def test_edges_sorted_canonical_thresholded():
    _, edges = _edges()
    assert edges == sorted(edges)
    for i, j, w in edges:
        assert i < j
        assert w >= PARAMS.tau


def test_deterministic():
    _, e1 = _edges()
    _, e2 = _edges()
    assert e1 == e2


def test_higher_tau_fewer_edges():
    _, lo = _edges(ClusteringParams(tau=0.3))
    _, hi = _edges(ClusteringParams(tau=0.7))
    assert len(hi) <= len(lo)


def test_knn_cap_limits_degree():
    feats, edges = _edges(ClusteringParams(knn_cap=2))
    from collections import Counter

    deg: Counter = Counter()
    for i, j, _ in edges:
        deg[i] += 1
        deg[j] += 1
    # kNN cap is per-direction; undirected degree can be up to 2*cap.
    assert all(d <= 2 * 2 for d in deg.values())


def test_atlas_threads_connected():
    feats, edges = _edges()
    # There should be at least one edge (Atlas threads are similar).
    assert len(edges) >= 1
