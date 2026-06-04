"""Stage C — pairwise similarity (spec 03 §7).

Five components blended by ``params.weights()`` (which sum to 1.0): weighted
participant Jaccard (IDF), embedding cosine, keyword Jaccard, temporal affinity,
attachment/link Jaccard. Weights are config, never constants (decision §16).
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

from .params import PARAMS, ClusteringParams


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def weighted_jaccard(a, b, w: dict) -> float:
    """Participant Jaccard weighted by IDF (``w`` maps person_id -> idf)."""
    inter = sum(w.get(x, 1.0) for x in (a & b))
    union = sum(w.get(x, 1.0) for x in (a | b))
    return inter / union if union else 0.0


def temporal_affinity(
    as_: datetime, ae: datetime, bs: datetime, be: datetime, tau_days: float = 14.0
) -> float:
    inter = (min(ae, be) - max(as_, bs)).total_seconds()
    span = (max(ae, be) - min(as_, bs)).total_seconds()
    if inter > 0:  # overlapping intervals -> IoU
        return inter / span if span > 0 else 1.0
    gap = (max(as_, bs) - min(ae, be)).total_seconds() / 86400.0  # disjoint, days
    return 0.3 * float(np.exp(-((gap / tau_days) ** 2)))


def thread_similarity(a, b, pidf: dict, params: ClusteringParams = PARAMS) -> float:
    w = params.weights()
    s_part = weighted_jaccard(a.participants, b.participants, pidf)
    s_emb = max(0.0, float(np.dot(a.embedding, b.embedding)))  # cosine; clamp negatives
    s_kw = jaccard(a.keywords, b.keywords)
    s_temp = temporal_affinity(
        a.t_start, a.t_end, b.t_start, b.t_end, params.tau_days
    )
    s_att = jaccard(
        a.attachment_hashes | a.link_domains, b.attachment_hashes | b.link_domains
    )
    return (
        w["part"] * s_part
        + w["emb"] * s_emb
        + w["kw"] * s_kw
        + w["temp"] * s_temp
        + w["attach"] * s_att
    )
