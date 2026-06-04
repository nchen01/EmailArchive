"""Clustering parameters (spec 03 §16).

All tuning values live here. Nothing in the logic modules is hardcoded; every
threshold/weight is read from a ``ClusteringParams`` instance. ``hash()`` gives a
stable digest stored in ``run_meta`` so any result is reproducible.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ClusteringParams:
    seed: int = 42
    # similarity weights (must sum to 1.0)
    w_part: float = 0.35
    w_emb: float = 0.30
    w_kw: float = 0.20
    w_temp: float = 0.10
    w_attach: float = 0.05
    # temporal affinity decay constant (days)
    tau_days: float = 14.0
    # blocking
    drop_frac: float = 0.5
    max_block: int = 300
    ann_k: int = 25
    # graph
    tau: float = 0.45
    knn_cap: int = 15
    # community detection
    gamma: float = 1.0
    # soft membership
    ratio: float = 0.65
    min_aff: float = 0.35
    # confidence
    min_solid: int = 4
    confidence_sample: int = 200
    # incremental
    assign_thresh: float = 0.45
    recluster_at: float = 0.15
    j_thresh: float = 0.5
    # sparse fallback threshold (below this -> agglomerative on dense matrix)
    min_threads_leiden: int = 30

    def weights(self) -> dict[str, float]:
        """Similarity component weights as the dict the similarity module expects."""
        return {
            "part": self.w_part,
            "emb": self.w_emb,
            "kw": self.w_kw,
            "temp": self.w_temp,
            "attach": self.w_attach,
        }

    def hash(self) -> str:
        d = {k: v for k, v in asdict(self).items()}
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


PARAMS = ClusteringParams()
