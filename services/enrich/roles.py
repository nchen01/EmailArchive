"""L1 §5 — role inference. Classify each Person into a Role with a confidence.

Rules-based v1: transparent and debuggable. Strong signals first; fall back to
UNKNOWN rather than guess. Upgrade to a classifier once labeled data exists.

Signal priority (spec 01 §5):
  1. Internal/external split — domain vs. internal_domains (strongest, ~100%).
  2. Legal domain membership — external legal counsel → VENDOR.
  3. Keyword signals in messages the contact sent:
       MANAGER  sign-off / approval / escalation (internal only)
       VENDOR   SOW / redlines / statement of work
       AE       renewal / seat count / discount tier
       LEAD     eval / partner / interested in / prospect
  4. No signal above threshold → UNKNOWN.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ekc_schemas import Person, Role

from .params import EnrichParams

# ── Signal patterns ───────────────────────────────────────────────────────────

_MANAGER = re.compile(
    r"\bsign[-\s]off\b"
    r"|\bsign(?:ed|ing)\s+off\b"
    r"|\bapproval\b"          # noun: implies approval-as-process; "approved" is too common
    r"|\bescalat",
    re.I,
)

# AE: renewal / account management vocabulary.  Deliberately narrow to avoid
# "contract review" (legal) triggering the AE bucket.
_AE = re.compile(
    r"\brenewal\b|\brenew\b"
    r"|\bseat\s+count\b"
    r"|\bdiscount\s+tier\b",
    re.I,
)

# LEAD: prospect / partnership vocabulary.
_LEAD = re.compile(
    r"\beval\b|\bevaluation\b"
    r"|\bdemo\b"
    r"|\bpartner\b"
    r"|\binterested\s+in\b"
    r"|\bprospect\b",
    re.I,
)

# VENDOR: statement-of-work / implementation vocabulary.
_VENDOR = re.compile(
    r"\bSOW\b"
    r"|\bstatement\s+of\s+work\b"
    r"|\bredlines?\b",
    re.I,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _domain_of(email: str) -> str:
    return email.split("@", 1)[1].lower() if "@" in email else ""


def _build_blobs(messages: list, email_to_pid: dict[str, str]) -> dict[str, str]:
    """Concatenate subject + clean_text of every non-noise message a person sent."""
    blobs: dict[str, list[str]] = defaultdict(list)
    for msg in messages:
        if msg.noise:
            continue
        pid = email_to_pid.get(msg.sender.email)
        if pid:
            blobs[pid].append(f"{msg.subject} {msg.clean_text}")
    return {pid: " ".join(parts) for pid, parts in blobs.items()}


# ── public API ────────────────────────────────────────────────────────────────

def infer_roles(
    people: list[Person],
    messages: list,
    email_to_pid: dict[str, str],
    internal_domains: list[str],
    params: EnrichParams,
) -> list[Person]:
    """Return a new list of Person objects with role + role_confidence filled in.

    Pure function: does not mutate its inputs.
    """
    internal_set = {d.lower() for d in internal_domains}
    legal_set    = {d.lower() for d in params.legal_domains}

    blobs = _build_blobs(messages, email_to_pid)

    updated: list[Person] = []
    for person in people:
        domain = _domain_of(person.canonical_email)
        blob   = blobs.get(person.id, "")

        if domain in internal_set:
            # Internal: check for manager signals in messages they sent.
            if blob and _MANAGER.search(blob):
                role, conf = Role.MANAGER, 0.7
            else:
                role, conf = Role.INTERNAL, 0.9

        else:
            # External: legal domain takes precedence over keyword checks.
            if domain in legal_set:
                role, conf = Role.VENDOR, 0.9
            elif _VENDOR.search(blob):
                role, conf = Role.VENDOR, 0.85
            elif _AE.search(blob):
                role, conf = Role.AE, 0.80
            elif _LEAD.search(blob):
                role, conf = Role.LEAD, 0.75
            else:
                role, conf = Role.UNKNOWN, 0.0

        # Gate low-confidence inferences to UNKNOWN (spec 01 §5).
        if role != Role.UNKNOWN and conf < params.role_confidence_threshold:
            role, conf = Role.UNKNOWN, 0.0

        updated.append(person.model_copy(update={"role": role, "role_confidence": conf}))

    return updated
