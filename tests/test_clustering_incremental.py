"""Ticket 3.9 — incremental.py (carry-over + orphan trigger, spec 03 §14)."""
from __future__ import annotations

from services.enrich.clustering.incremental import (
    carry_over_ids,
    orphan_ratio,
    should_recluster,
)
from services.enrich.clustering.params import ClusteringParams

PARAMS = ClusteringParams()


def test_carry_over_inherits_on_high_jaccard():
    old = [{"id": "OLD1", "thread_ids": ["a", "b", "c"]}]
    new = [{"id": "NEW1", "thread_ids": ["a", "b", "c", "d"]}]  # j = 3/4 >= 0.5
    carry_over_ids(new, old, PARAMS)
    assert new[0]["id"] == "OLD1"


def test_carry_over_skips_on_low_jaccard():
    old = [{"id": "OLD1", "thread_ids": ["a", "b", "c"]}]
    new = [{"id": "NEW1", "thread_ids": ["x", "y"]}]  # j = 0
    carry_over_ids(new, old, PARAMS)
    assert new[0]["id"] == "NEW1"


def test_carry_over_one_to_one():
    old = [
        {"id": "OLD1", "thread_ids": ["a", "b"]},
        {"id": "OLD2", "thread_ids": ["c", "d"]},
    ]
    new = [
        {"id": "N1", "thread_ids": ["a", "b"]},
        {"id": "N2", "thread_ids": ["c", "d"]},
    ]
    carry_over_ids(new, old, PARAMS)
    ids = {p["id"] for p in new}
    assert ids == {"OLD1", "OLD2"}  # each old id used at most once


def test_id_stability_5pct_added(monkeypatch=None):
    """DoD §22: >=90% of project ids stable when 5% of threads are added."""
    # Old run: 20 projects with disjoint thread sets.
    old = [{"id": f"P{i}", "thread_ids": [f"t{i}_a", f"t{i}_b"]} for i in range(20)]
    # New run: same clusters, one gains an extra thread (5% growth on that cluster).
    new = [{"id": f"X{i}", "thread_ids": list(p["thread_ids"])} for i, p in enumerate(old)]
    new[0]["thread_ids"].append("t0_c")
    carry_over_ids(new, old, PARAMS)
    stable = sum(1 for p in new if p["id"].startswith("P"))
    assert stable / len(new) >= 0.9


def test_orphan_ratio_and_recluster_trigger():
    assert orphan_ratio(0, 0) == 0.0
    assert orphan_ratio(3, 10) == 0.3
    # default recluster_at = 0.15
    assert should_recluster(2, 10, PARAMS) is True
    assert should_recluster(1, 10, PARAMS) is False


def test_carry_over_deterministic():
    old = [{"id": "OLD1", "thread_ids": ["a", "b", "c"]}]
    new1 = [{"id": "N", "thread_ids": ["a", "b", "c"]}]
    new2 = [{"id": "N", "thread_ids": ["a", "b", "c"]}]
    carry_over_ids(new1, old, PARAMS)
    carry_over_ids(new2, old, PARAMS)
    assert new1 == new2
