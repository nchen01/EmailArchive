"""Stage G — sensitivity tagging (spec 00 §11). Conservative rules floor.

Tag ordering follows the Sensitivity enum declaration order
(PRIVILEGED, LEGAL, HR, PERSONAL), so pmsg_016 → ["privileged", "legal"].
"""
from __future__ import annotations

import re

from ekc_schemas import Sensitivity

from ..params import IngestParams

PRIVILEGE = ["attorney-client", "privileged and confidential", "legal hold", "outside counsel"]
HR = [
    "compensation",
    "salary",
    "performance review",
    "performance improvement plan",  # D7: expanded from bare "pip"; see docs/decisions.md
    "termination",
    "severance",
    "offer letter",
    "benefits enrollment",
]

# Canonical emit order for tags.
_ORDER = [
    Sensitivity.PRIVILEGED,
    Sensitivity.LEGAL,
    Sensitivity.HR,
    Sensitivity.PERSONAL,
]


def _build_matcher(keywords: list[str]) -> re.Pattern:
    # Word-boundary match so short tokens (e.g. "pip") don't match inside
    # unrelated words (e.g. "datapipe"). Phrases match literally.
    alternation = "|".join(re.escape(k) for k in keywords)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


_PRIVILEGE_RE = _build_matcher(PRIVILEGE)
_HR_RE = _build_matcher(HR)


def _domain_of(email: str) -> str:
    return email.split("@", 1)[1] if "@" in email else ""


def _domain_in(email: str, domains: list[str]) -> bool:
    return _domain_of(email) in {d.lower() for d in domains}


def _looks_work_related(blob: str) -> bool:
    work_cues = ("meeting", "project", "deadline", "report", "review", "contract", "client")
    return any(c in blob for c in work_cues)


def tag_sensitivity(
    subject: str,
    clean_text: str,
    sender_email: str,
    cfg: IngestParams,
) -> list[Sensitivity]:
    tags: set[Sensitivity] = set()
    blob = f"{subject} {clean_text}".lower()

    if _PRIVILEGE_RE.search(blob) or _domain_in(sender_email, cfg.legal_domains):
        tags.add(Sensitivity.PRIVILEGED)
    if _domain_in(sender_email, cfg.legal_domains):
        tags.add(Sensitivity.LEGAL)
    if _HR_RE.search(blob) or sender_email in {s.lower() for s in cfg.hr_senders}:
        tags.add(Sensitivity.HR)
    if _domain_in(sender_email, cfg.personal_domains) and not _looks_work_related(blob):
        tags.add(Sensitivity.PERSONAL)

    ordered = [t for t in _ORDER if t in tags]
    return ordered or [Sensitivity.NONE]
