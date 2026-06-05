"""Stage I — project confidence (spec 03 §13).

Correction B (binding): the cohesion pair sample must be deterministic. The
sampled ``pairs`` are **sorted** before ``random.Random(params.seed).sample`` so
the same seed yields the same sample (unordered iteration would not), and the
seed comes from ``params.seed`` (not a hardcoded 42).

confidence = 0.5*cohesion + 0.3*separation + 0.2*size_factor, clipped to [0, 1].
Singletons get the cohesion default (0.5) and a small size_factor → low overall.
"""
from __future__ import annotations

import random

import numpy as np

from .params import PARAMS, ClusteringParams


def project_confidence(
    feats,
    project_thread_idxs: list[int],
    prof: dict,
    this_c,
    params: ClusteringParams = PARAMS,
):
    idxs = sorted(project_thread_idxs)

    # Cohesion: mean pairwise embedding cosine within the cluster (sampled if large).
    pairs = sorted((a, b) for k, a in enumerate(idxs) for b in idxs[k + 1:])
    if len(pairs) > params.confidence_sample:
        pairs = random.Random(params.seed).sample(pairs, params.confidence_sample)
    cohesion = (
        float(np.mean([np.dot(feats[a].embedding, feats[b].embedding) for a, b in pairs]))
        if pairs
        else 0.5
    )

    # Separation: 1 - max cosine of this centroid to any other centroid.
    me = prof[this_c]["emb"]
    others = [
        float(np.dot(me, prof[c]["emb"])) for c in sorted(prof) if c != this_c
    ]
    separation = 1.0 - (max(others) if others else 0.0)

    size_factor = min(1.0, len(idxs) / params.min_solid)
    conf = 0.5 * cohesion + 0.3 * separation + 0.2 * size_factor
    debug = dict(
        cohesion=round(cohesion, 3),
        separation=round(separation, 3),
        n_threads=len(idxs),
    )
    return round(float(np.clip(conf, 0.0, 1.0)), 3), debug
