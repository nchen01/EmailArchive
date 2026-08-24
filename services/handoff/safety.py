"""Deterministic privacy/safety review findings for a generated handoff package (S44).

Creator-side, pre-publish. A pure, offline, LLM-free scan of the package's OWN
snapshot (claim text + safe evidence snapshots) that flags likely
credential/secret, payment, personal, HR/legal, security-sensitive, stale/
conflicting, blocker, and low-confidence content so the creator can prune or
acknowledge it before publishing. It never reads the live mailbox, never calls an
external API, and never exposes the matched sensitive text - a finding carries only
a safe category, severity, a fixed explanation, and a package-local reference
(claim id or message_id_header).

Findings are computed on demand from rows the caller already loaded; nothing is
persisted (a finding always reflects the CURRENT package, so pruning + regenerate
makes it disappear). The scan runs over content that already passed the
sensitivity/noise/exclusion gates, so it is a second, content-pattern layer that
catches risks the coarse thread/message gates miss (e.g. an API key pasted into an
otherwise-normal thread). It never re-introduces excluded content.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Severity order for sorting / gating. Only "high" blocks publish.
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
LOW_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True)
class Finding:
    id: str
    category: str
    severity: str
    explanation: str  # fixed per rule; NEVER the matched text
    claim_id: str | None = None
    evidence_header: str | None = None


def _fid(category: str, ref: str) -> str:
    return hashlib.sha1(f"{category}|{ref}".encode()).hexdigest()[:12]


# -- text rules: (category, severity, safe explanation, compiled pattern) -------
# High-precision, curated patterns; the explanation never echoes the match.
_CREDENTIAL = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bghp_[A-Za-z0-9]{20,}\b"
    r"|\bxox[baprs]-[A-Za-z0-9-]{10,}\b"
    r"|\bsk-[A-Za-z0-9]{16,}\b"
    r"|(?i:\b(?:api[_-]?key|secret|password|passwd|access[_-]?token|bearer)\b\s*[:=]\s*\S{6,})",
)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_PERSONAL_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_MEDICAL = re.compile(
    r"(?i)\b(diagnos(is|ed)|prescri(ption|bed)|chemotherapy|\bhiv\b|cancer|"
    r"pregnan(t|cy)|mental health|therapy session|medical leave)\b"
)
_HR_LEGAL = re.compile(
    r"(?i)\b(terminat(e|ed|ion)|layoff|laid off|severance|compensation|salary|"
    r"lawsuit|litigation|attorney[- ]client|disciplinary|performance improvement plan|"
    r"\bpip\b|harassment|grievance)\b"
)
_SECURITY = re.compile(
    r"(?i)(\bvulnerabilit(y|ies)\b|\bexploit(ed|s)?\b|zero[- ]day|data breach|"
    r"security breach|\bbreach\b|\bcve-\d|backdoor|\bmalware\b|customer[- ]confidential)"
)
_BLOCKER = re.compile(
    r"(?i)\b(blocked|blocker|blocking|stalled|waiting on|depends on|dependency|"
    r"dependencies|awaiting sign[- ]?off)\b"
)
_STALE = re.compile(
    r"(?i)(\bstale\b|\boutdated\b|no longer|superseded|obsolete|deprecated|revisited|"
    r"switch(ing)? .*? from .*? to |instead of )"
)

# Personal email domains (a cited message from one is worth a heads-up).
_PERSONAL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com",
}

_EXPL = {
    "credential_or_secret": "Looks like an API key, token, password, or private key.",
    "payment_financial": "Looks like a card, bank, or payment identifier.",
    "personal_sensitive_id": "Looks like a government ID / SSN-style number.",
    "personal_sensitive_medical": "Mentions medical / health information.",
    "personal_sensitive_domain": "Cited message is from a personal email domain.",
    "hr_legal": "Mentions HR / legal / employment / compensation topics.",
    "security_sensitive": "Mentions a vulnerability, breach, exploit, or incident.",
    "stale_or_conflicting": "May be outdated or contradict another claim.",
    "blocker_or_dependency": "Reads as a blocker/dependency but is not typed as one.",
    "low_confidence_or_needs_confirmation": "Weakly supported claim; confirm before relying on it.",
}


def _luhn_ok(digits: str) -> bool:
    ds = [int(c) for c in digits]
    if not (13 <= len(ds) <= 19):
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _has_payment(text: str) -> bool:
    if _IBAN.search(text or ""):
        return True
    for m in _CARD_CANDIDATE.finditer(text or ""):
        if _luhn_ok(re.sub(r"\D", "", m.group())):
            return True
    return False


def scan_package(claims: list[dict], evidence: list[dict]) -> list[Finding]:
    """Deterministically scan a package's claims + evidence for safety findings.

    ``claims``: dicts with keys id, kind, text, confidence.
    ``evidence``: dicts with keys header, subject, body, sender_domain.
    Deduped by (category, reference); sorted high -> low then category.
    """
    out: dict[str, Finding] = {}

    def add(category: str, severity: str, explanation: str, *, claim_id=None, header=None):
        ref = claim_id or header or ""
        fid = _fid(category, ref)
        out.setdefault(fid, Finding(id=fid, category=category, severity=severity,
                                    explanation=explanation, claim_id=claim_id,
                                    evidence_header=header))

    def scan_text(text: str, *, claim_id=None, header=None):
        if _CREDENTIAL.search(text):
            add("credential_or_secret", "high", _EXPL["credential_or_secret"], claim_id=claim_id, header=header)
        if _has_payment(text):
            add("payment_financial", "high", _EXPL["payment_financial"], claim_id=claim_id, header=header)
        if _PERSONAL_SSN.search(text):
            add("personal_sensitive", "high", _EXPL["personal_sensitive_id"], claim_id=claim_id, header=header)
        if _MEDICAL.search(text):
            add("personal_sensitive", "medium", _EXPL["personal_sensitive_medical"], claim_id=claim_id, header=header)
        if _HR_LEGAL.search(text):
            add("hr_legal", "medium", _EXPL["hr_legal"], claim_id=claim_id, header=header)
        if _SECURITY.search(text):
            add("security_sensitive", "medium", _EXPL["security_sensitive"], claim_id=claim_id, header=header)

    for c in claims:
        cid = c.get("id")
        text = c.get("text") or ""
        scan_text(text, claim_id=cid)
        if c.get("kind") != "blocker" and _BLOCKER.search(text):
            add("blocker_or_dependency", "medium", _EXPL["blocker_or_dependency"], claim_id=cid)
        if _STALE.search(text):
            add("stale_or_conflicting", "medium", _EXPL["stale_or_conflicting"], claim_id=cid)
        if float(c.get("confidence", 1.0)) < LOW_CONFIDENCE_THRESHOLD:
            add("low_confidence_or_needs_confirmation", "low",
                _EXPL["low_confidence_or_needs_confirmation"], claim_id=cid)

    for e in evidence:
        header = e.get("header")
        blob = f"{e.get('subject') or ''} {e.get('body') or ''}"
        scan_text(blob, header=header)
        if (e.get("sender_domain") or "").lower() in _PERSONAL_DOMAINS:
            add("personal_sensitive", "medium", _EXPL["personal_sensitive_domain"], header=header)

    return sorted(out.values(), key=lambda f: (SEVERITY_ORDER[f.severity], f.category, f.id))


def high_severity(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "high"]
