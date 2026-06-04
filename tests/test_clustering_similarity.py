"""Ticket 3.3 — similarity components (spec 03 §7)."""
from __future__ import annotations

from datetime import datetime, timezone

from services.enrich.clustering.params import ClusteringParams
from services.enrich.clustering.similarity import (
    jaccard,
    temporal_affinity,
    thread_similarity,
    weighted_jaccard,
)


def _dt(d):
    return datetime(2026, 4, d, tzinfo=timezone.utc)


def test_jaccard_basic():
    assert jaccard(frozenset(), frozenset()) == 0.0
    assert jaccard(frozenset("ab"), frozenset("ab")) == 1.0
    assert jaccard(frozenset("ab"), frozenset("bc")) == 1 / 3


def test_weighted_jaccard_idf():
    a, b = frozenset({"x", "y"}), frozenset({"y", "z"})
    # Equal weights -> plain jaccard = 1/3.
    assert abs(weighted_jaccard(a, b, {}) - 1 / 3) < 1e-9
    # Heavier shared term raises similarity.
    w = {"y": 10.0, "x": 1.0, "z": 1.0}
    assert weighted_jaccard(a, b, w) > 1 / 3


def test_temporal_overlap_and_gap():
    # Identical interval -> IoU 1.0
    assert abs(temporal_affinity(_dt(1), _dt(3), _dt(1), _dt(3)) - 1.0) < 1e-9
    # Disjoint intervals decay below the overlap credit.
    far = temporal_affinity(_dt(1), _dt(2), _dt(20), _dt(21), tau_days=14.0)
    assert 0.0 <= far < 0.3


class _F:
    def __init__(self, parts, kws, emb, ts, te, att=frozenset(), links=frozenset()):
        import numpy as np

        self.participants = frozenset(parts)
        self.keywords = frozenset(kws)
        self.embedding = np.asarray(emb, dtype="float32")
        self.t_start = ts
        self.t_end = te
        self.attachment_hashes = frozenset(att)
        self.link_domains = frozenset(links)


def test_thread_similarity_bounds_and_weights():
    import numpy as np

    p = ClusteringParams()
    e = np.array([1.0, 0.0], dtype="float32")
    a = _F({"p1"}, {"atlas"}, e, _dt(1), _dt(2))
    b = _F({"p1"}, {"atlas"}, e, _dt(1), _dt(2))
    s = thread_similarity(a, b, {"p1": 1.0}, p)
    assert 0.0 <= s <= 1.0
    # Identical threads -> high similarity (all components ~1 except attach=0, weight 0.05).
    assert s >= 0.9


def test_dissimilar_threads_low():
    import numpy as np

    p = ClusteringParams()
    a = _F({"p1"}, {"atlas"}, [1.0, 0.0], _dt(1), _dt(2))
    b = _F({"p2"}, {"borealis"}, [0.0, 1.0], _dt(20), _dt(21))
    s = thread_similarity(a, b, {"p1": 1.0, "p2": 1.0}, p)
    assert s < 0.2


def test_weights_configurable():
    import numpy as np

    e = np.array([1.0, 0.0], dtype="float32")
    a = _F({"p1"}, set(), e, _dt(1), _dt(2))
    b = _F({"p2"}, set(), e, _dt(1), _dt(2))
    # With high emb weight, identical embeddings dominate.
    hi_emb = ClusteringParams(w_part=0.0, w_emb=1.0, w_kw=0.0, w_temp=0.0, w_attach=0.0)
    assert thread_similarity(a, b, {}, hi_emb) >= 0.99
    # With high participant weight, disjoint participants -> low.
    hi_part = ClusteringParams(w_part=1.0, w_emb=0.0, w_kw=0.0, w_temp=0.0, w_attach=0.0)
    assert thread_similarity(a, b, {}, hi_part) == 0.0
