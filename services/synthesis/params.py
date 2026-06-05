"""L3 synthesis parameters (S4 ticket 4.6).

Model id and cost/size guardrails. The model id is **config, not code** (S4
guide §9): changing it is a config change. Same id as event extraction (D10),
one cost bucket.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SynthesisParams:
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 1024
    max_context_messages: int = 20  # cap context size (cost control, §5/§11)
    timeout_s: float = 30.0


# Default instance used across the synthesis layer.
PARAMS = SynthesisParams()
