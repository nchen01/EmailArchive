"""L1 enrichment parameters (spec 01 §3, §4, §5)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EnrichParams:
    name_similarity_threshold: float = 0.92  # display-name fuzzy merge (spec 01 §3)
    edge_half_life_days: float = 45.0        # recency decay constant (spec 01 §4)
    volume_weight: float = 0.5
    recency_weight: float = 0.3
    recip_weight: float = 0.2
    # Role inference (spec 01 §5)
    legal_domains: list[str] = field(default_factory=list)
    role_confidence_threshold: float = 0.4   # below this → Role.UNKNOWN
