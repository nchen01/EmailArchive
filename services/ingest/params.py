"""Ingest parameters & defaults (spec 00 §14). Nothing hardcoded in logic paths."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IngestParams:
    batch_size: int = 100
    max_backoff_s: int = 64
    clean_text_max_chars: int = 50_000
    attachment_retain: bool = False
    noise_reply_ratio: float = 0.0
    sensitivity_mode: str = "exclude"   # "exclude" | "redact" | "tag-only"
    retention_days: int | None = None
    # Per-tenant sensitivity config (passed in at runtime)
    legal_domains: list[str] = field(default_factory=list)
    hr_senders: list[str] = field(default_factory=list)
    personal_domains: list[str] = field(default_factory=list)
    owner_domains: list[str] = field(default_factory=list)
