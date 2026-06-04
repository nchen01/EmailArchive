"""Stage E + F — community detection (Leiden) and soft membership (spec 03 §9–§10).

Leiden gives a deterministic hard partition under a fixed seed; soft membership
recovers overlap by scoring each thread against every community *profile* and
attaching it to additional communities nearly as good as its primary.

Determinism notes (decision H):
- Isolated nodes (no surviving edges) each become their own singleton community.
- Community ids are renumbered into a canonical, contiguous order keyed by the
  sorted thread indices of each community, so the id space does not depend on
  Leiden's internal iteration order.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .params import PARAMS, ClusteringParams
from .similarity import jaccard, overlap_coeff, temporal_affinity, weighted_jaccard


def detect_communities(
    n_nodes: int, edges, params: ClusteringParams = PARAMS
) -> tuple[list[int], float]:
    """Return ``(membership, modularity)``; ``membership[node]`` = community id."""
    import igraph as ig
    import leidenalg as la

    g = ig.Graph(n=n_nodes, edges=[(i, j) for i, j, _ in edges])
    if edges:
        g.es["weight"] = [w for _, _, w in edges]
        part = la.find_partition(
            g,
            la.RBConfigurationVertexPartition,
            weights="weight",
            resolution_parameter=params.gamma,
            seed=params.seed,
            n_iterations=-1,
        )
        membership = list(part.membership)
        modularity = float(part.modularity)
    else:
        membership = list(range(n_nodes))
        modularity = 0.0

    return _canonicalize(membership), modularity


def _canonicalize(membership: list[int]) -> list[int]:
    """Renumber communities into a deterministic, contiguous id space.

    Order communities by their smallest member index so the mapping is stable
    regardless of the upstream label values.
    """
    members: dict = defaultdict(list)
    for node, c in enumerate(membership):
        members[c].append(node)
    order = sorted(members, key=lambda c: min(members[c]))
    remap = {old: new for new, old in enumerate(order)}
    return [remap[c] for c in membership]


def community_profiles(feats, membership: list[int]) -> dict:
    by_c: dict = defaultdict(list)
    for i, c in enumerate(membership):
        by_c[c].append(i)
    prof: dict = {}
    for c in sorted(by_c):
        idxs = by_c[c]
        emb = np.mean([feats[i].embedding for i in idxs], axis=0)
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        prof[c] = {
            "emb": emb.astype("float32"),
            "participants": frozenset().union(*(feats[i].participants for i in idxs)),
            "keywords": frozenset().union(*(feats[i].keywords for i in idxs)),
            "t_start": min(feats[i].t_start for i in idxs),
            "t_end": max(feats[i].t_end for i in idxs),
            "members": sorted(idxs),
        }
    return prof


def _affinity(f, pr, pidf, params: ClusteringParams) -> float:
    w = params.weights()
    s_part = weighted_jaccard(f.participants, pr["participants"], pidf)
    s_emb = max(0.0, float(np.dot(f.embedding, pr["emb"])))
    s_kw = (
        overlap_coeff(f.keywords, pr["keywords"])
        if params.kw_overlap
        else jaccard(f.keywords, pr["keywords"])
    )
    s_temp = temporal_affinity(
        f.t_start, f.t_end, pr["t_start"], pr["t_end"], params.tau_days
    )
    # Attachment/link signal folds into the profile via keywords; the §10 affinity
    # mirrors thread_similarity minus the attach term (profiles don't carry hashes).
    return w["part"] * s_part + w["emb"] * s_emb + w["kw"] * s_kw + w["temp"] * s_temp


def soft_assign(
    feats, membership: list[int], prof: dict, pidf, params: ClusteringParams = PARAMS
) -> dict:
    """thread_idx -> {community_id: weight}. Primary = argmax affinity per thread.

    A thread always keeps its primary; it joins a secondary community ``c`` iff
    ``aff(c) >= min_aff`` and ``aff(c) >= ratio * top``.
    """
    ratio, min_aff = params.ratio, params.min_aff
    assigns: dict = {}
    comm_ids = sorted(prof)
    for i in range(len(feats)):
        f = feats[i]
        affs = {c: _affinity(f, prof[c], pidf, params) for c in comm_ids}
        # Deterministic argmax: highest affinity, ties broken by lowest community id.
        primary = max(comm_ids, key=lambda c: (affs[c], -c))
        top = affs[primary]
        out = {primary: round(max(top, 1e-3), 4)}
        for c in comm_ids:
            if c != primary and affs[c] >= min_aff and affs[c] >= ratio * top:
                out[c] = round(affs[c], 4)
        assigns[i] = out
    return assigns
