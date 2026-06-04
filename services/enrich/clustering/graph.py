"""Stage D — similarity graph (spec 03 §8).

Score candidate pairs, keep edges >= ``tau``, then kNN-sparsify (keep each node's
top-``knn_cap`` edges) to keep Leiden well-behaved. Returns a deterministic
*sorted* edge list of ``(i, j, weight)`` with ``i < j`` (decision H).
"""
from __future__ import annotations

from collections import defaultdict

from .params import PARAMS, ClusteringParams
from .similarity import thread_similarity


def build_edges(feats, pairs, pidf, params: ClusteringParams = PARAMS):
    tau = params.tau
    knn_cap = params.knn_cap

    scored: list[tuple[int, int, float]] = []
    for i, j in pairs:
        s = thread_similarity(feats[i], feats[j], pidf, params)
        if s >= tau:
            scored.append((i, j, s))

    # kNN sparsify: keep each node's top-knn_cap edges. Tie-break deterministically
    # by (-weight, other-node) so the truncation is stable.
    per_node: dict = defaultdict(list)
    for i, j, s in scored:
        per_node[i].append((s, j))
        per_node[j].append((s, i))

    keep: set = set()
    for node in sorted(per_node):
        lst = sorted(per_node[node], key=lambda sj: (-sj[0], sj[1]))
        for s, other in lst[:knn_cap]:
            keep.add((min(node, other), max(node, other), round(s, 6)))

    return sorted(keep)
