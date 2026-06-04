"""L1 project clustering (spec 03).

Public surface:
- ``ClusteringParams`` / ``PARAMS`` — tuning knobs (spec 03 §16).
- ``ThreadFeatures`` / ``build_thread_features`` — input contract (§5).
- ``cluster_mailbox`` — orchestrator producing a ``ClusteringResult`` (§15).

Everything else is internal and may be refactored freely.
"""
from __future__ import annotations

from .params import PARAMS, ClusteringParams

__all__ = ["PARAMS", "ClusteringParams"]
