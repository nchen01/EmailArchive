"""Deterministic package-local recipient ask (S17.9).

The recipient queries ONLY the published handoff package, never the covered
employee's mailbox. This module is a pure, LLM-free term-overlap retrieval over
the package's own snapshotted ``HandoffEvidence`` + ``HandoffClaim`` rows.

Hard contract:

    No package evidence, no answer. Every citation is a HandoffEvidence
    ``message_id_header`` from THIS package.

Because the package snapshot already excludes sensitive / out-of-scope content
(applied at generation, S17.3), a query about a sensitive or unknown topic
simply finds no match and yields the IDENTICAL neutral no-evidence result — so
this path can never act as an existence oracle for excluded content.

It is a pure function over rows the caller already loaded package-locally; it
never reads Message/Thread/Project/Person/Event/retrieval/Gmail state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TS_MIN = datetime.min.replace(tzinfo=timezone.utc)
_MIN_TERM_LEN = 3
_MAX_EVIDENCE = 8
_MAX_CLAIMS = 6

# Common words carry no retrieval signal; dropping them keeps a short query like
# "what is the atlas status" from matching every card on "is/the".
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "this", "that", "these",
    "those", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in",
    "on", "for", "with", "at", "by", "from", "as", "it", "its", "into", "about",
    "what", "who", "whom", "when", "where", "why", "how", "do", "does", "did",
    "can", "could", "should", "would", "will", "i", "you", "we", "they", "he",
    "she", "him", "her", "them", "our", "your", "their", "me", "my", "any",
    "some", "all", "have", "has", "had", "not", "no", "yes", "please", "tell",
    "give", "there", "here", "which", "whose", "get", "know", "need", "want",
})


def _terms(text: str) -> set[str]:
    """Lowercased alnum tokens, minus stopwords and very short tokens."""
    return {
        t for t in _TOKEN_RE.findall((text or "").lower())
        if len(t) >= _MIN_TERM_LEN and t not in _STOPWORDS
    }


@dataclass
class AskResult:
    """Deterministic package-local answer. ``answered`` is False iff no package
    evidence grounds the query (identical to the sensitive/unknown case)."""
    answered: bool
    claims: list  # matching HandoffClaim rows (grounded), capped
    evidence: list  # matching + cited HandoffEvidence rows (the citations), capped


def answer_from_package(query: str, claims: list, evidence: list) -> AskResult:
    """Rank the package's own claims/evidence against ``query`` by term overlap.

    Returns the matching claims plus the evidence that grounds them (direct
    matches first, then the evidence those claims cite). Every returned evidence
    row is an in-package ``HandoffEvidence``; every returned claim is an in-package
    ``HandoffClaim``. If nothing matches, returns a no-answer result — never a
    guess, never a reason.
    """
    q = _terms(query)
    if not q:
        return AskResult(False, [], [])

    by_header = {e.message_id_header: e for e in evidence}

    def score(text: str) -> int:
        return len(q & _terms(text))

    # Rank evidence by term overlap over its safe, package-local fields only.
    ev_scored = []
    for e in evidence:
        s = score(" ".join([
            e.subject or "", e.body_snapshot or "",
            e.sender_display or "", e.sender_domain or "",
        ]))
        if s > 0:
            ev_scored.append((s, e))
    # Highest overlap first; ties broken by most-recent snapshot ts.
    ev_scored.sort(key=lambda p: (p[0], p[1].ts or _TS_MIN), reverse=True)

    # Rank claims by term overlap over claim text.
    cl_scored = [(score(c.text), c) for c in claims]
    cl_scored = [(s, c) for s, c in cl_scored if s > 0]
    cl_scored.sort(key=lambda p: p[0], reverse=True)
    selected_claims = [c for _, c in cl_scored[:_MAX_CLAIMS]]

    # Citation set = direct evidence matches, then evidence cited by the selected
    # claims — so every claim shown has its citations present, and every citation
    # resolves to an in-package HandoffEvidence row.
    ordered_headers: list[str] = []
    for _, e in ev_scored:
        if e.message_id_header not in ordered_headers:
            ordered_headers.append(e.message_id_header)
    for c in selected_claims:
        for h in c.source_message_id_headers:
            if h in by_header and h not in ordered_headers:
                ordered_headers.append(h)
    ordered_headers = ordered_headers[:_MAX_EVIDENCE]
    selected_evidence = [by_header[h] for h in ordered_headers]

    # Grounding rule: no package evidence -> no answer.
    if not selected_evidence:
        return AskResult(False, [], [])

    # Answer-local citation rule: every returned claim must cite at least one row
    # that is ACTUALLY in the returned evidence. A claim whose citations were all
    # pushed out by the _MAX_EVIDENCE cap is dropped rather than shown with a
    # dangling citation the recipient cannot see. (The endpoint additionally
    # narrows each claim's displayed headers to this same returned set.)
    returned = {e.message_id_header for e in selected_evidence}
    grounded_claims = [
        c for c in selected_claims
        if any(h in returned for h in c.source_message_id_headers)
    ]
    return AskResult(True, grounded_claims, selected_evidence)
