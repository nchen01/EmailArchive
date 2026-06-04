"""Stage J — ID stability & incremental updates (spec 03 §14).

Re-clustering must not reshuffle project ids. Two mechanisms:

1. ``carry_over_ids`` — full re-cluster: match new clusters to previous ones by
   Jaccard of thread sets and inherit the old id (and label override) on a match
   >= ``j_thresh``.
2. ``incremental_assign`` — new threads without a full re-cluster: score each new
   thread against existing community profiles; attach if top affinity >=
   ``assign_thresh``, else mark orphan. ``should_recluster`` fires when the orphan
   ratio exceeds ``recluster_at``.
"""
from __future__ import annotations

from .communities import _affinity
from .params import PARAMS, ClusteringParams


def carry_over_ids(new_projects, old_projects, params: ClusteringParams = PARAMS):
    """Inherit old project ids onto matching new projects (in place) and return them.

    Deterministic: new projects are processed in id order; for each, the best
    unused old id by Jaccard wins, ties broken by old id string.
    """
    j_thresh = params.j_thresh
    old = {p["id"]: set(p["thread_ids"]) for p in old_projects}
    used: set = set()

    for np_ in sorted(new_projects, key=lambda p: p["id"]):
        ns = set(np_["thread_ids"])
        best, best_j = None, 0.0
        for oid in sorted(old):
            if oid in used:
                continue
            os = old[oid]
            union = ns | os
            j = len(ns & os) / len(union) if union else 0.0
            # ">" with sorted iteration => first (lowest) old id wins ties.
            if j > best_j:
                best, best_j = oid, j
        if best is not None and best_j >= j_thresh:
            np_["id"] = best
            used.add(best)

    new_projects.sort(key=lambda p: p["id"])
    return new_projects


def incremental_assign(
    new_feats, prof: dict, pidf, params: ClusteringParams = PARAMS
):
    """Assign each new-thread feature to an existing community or mark it orphan.

    Returns ``(assignments, orphans)`` where ``assignments`` is a list of
    ``(thread_id, community_id, weight)`` and ``orphans`` is a list of thread_ids.
    """
    assign_thresh = params.assign_thresh
    comm_ids = sorted(prof)
    assignments: list[tuple] = []
    orphans: list[str] = []

    for f in sorted(new_feats, key=lambda x: x.thread_id):
        if not comm_ids:
            orphans.append(f.thread_id)
            continue
        affs = {c: _affinity(f, prof[c], pidf, params) for c in comm_ids}
        best = max(comm_ids, key=lambda c: (affs[c], -c))
        if affs[best] >= assign_thresh:
            assignments.append((f.thread_id, best, round(float(affs[best]), 4)))
        else:
            orphans.append(f.thread_id)

    return assignments, orphans


def orphan_ratio(n_orphans: int, n_total: int) -> float:
    return (n_orphans / n_total) if n_total else 0.0


def should_recluster(n_orphans: int, n_total: int, params: ClusteringParams = PARAMS) -> bool:
    return orphan_ratio(n_orphans, n_total) > params.recluster_at
