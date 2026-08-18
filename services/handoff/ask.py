"""Deterministic package-local recipient ask (S17.9 base; S40 intent shaping).

The recipient queries ONLY the published handoff package, never the covered
employee's mailbox. This module is a pure, LLM-free retrieval over the package's
own snapshotted ``HandoffEvidence`` + ``HandoffClaim`` rows.

Hard contract:

    No package evidence, no answer. Every citation is a HandoffEvidence
    ``message_id_header`` from THIS package.

Because the package snapshot already excludes sensitive / out-of-scope content
(applied at generation, S17.3), a query about a sensitive or unknown topic simply
finds no match and yields the IDENTICAL neutral no-evidence result - so this path
can never act as an existence oracle for excluded content. Oracle safety is
enforced BEFORE any intent shaping: if the query matches nothing in the package we
return the neutral no-answer regardless of intent.

S40 adds deterministic intent shaping over the matched, package-local rows:

    - "next steps" -> only open-loop claims (or an honest "none found").
    - "blocked"    -> blocker-shaped claims, or "no blockers found in the package".
    - "decisions"  -> decision/outcome claims only.
    - "status"     -> the overall state (decisions + open loops together).
    - otherwise    -> the prior general term-overlap answer.

When the query names an S39 project label, candidate claims are scoped to that
project so "next steps for Nexus" ranks Nexus work. Intent shaping only ever
selects among claims the recipient can already see in the coverage brief, so it
adds no new signal beyond what is already visible.

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

# Generated packages carry these claim kinds (services/handoff/generator.py maps
# proposed -> open_loop, did/outcome -> decision). Kept explicit so intent shaping
# is readable even if future kinds (blocker/project_state/...) start appearing.
_KIND_OPEN_LOOP = "open_loop"
_KIND_DECISION = "decision"
_KIND_BLOCKER = "blocker"

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

# ── intent classification (deterministic, order = specific-first) ─────────────
_RE_BLOCKED = re.compile(
    r"\b(block|blocked|blocker|blockers|blocking|stuck|stalled|waiting|"
    r"depend|dependency|dependencies|risk|risks|unresolved)\b"
)
_RE_NEXT = re.compile(
    r"\b(next\s+steps?|to-?dos?|action\s+items?|follow[-\s]?ups?|remaining|"
    r"outstanding|open\s+loops?|what'?s\s+left|what\s+needs)\b"
)
_RE_DECISION = re.compile(r"\b(decisions?|decided|outcomes?|concluded|agreed)\b")
_RE_STATUS = re.compile(
    r"\b(status|state|progress|standing|update|summary|overview|"
    r"where\s+(are|do|things|we))\b"
)
# Words in a claim's text that make an open loop read as a blocker/dependency.
_RE_BLOCKER_TEXT = re.compile(
    r"\b(block|blocked|blocker|blocking|stuck|stalled|wait|waiting|pending|"
    r"depend|dependenc|risk|unresolved|hold|held|need\s+sign)\b",
    re.I,
)


def detect_ask_intent(query: str) -> str:
    """Classify a recipient question into an answer shape.

    Returns one of: 'blocked', 'next_steps', 'decisions', 'status', 'general'.
    Specific intents win so "what's blocked" is not swallowed by a stray "status".
    """
    ql = (query or "").lower()
    if _RE_BLOCKED.search(ql):
        return "blocked"
    if _RE_NEXT.search(ql):
        return "next_steps"
    if _RE_DECISION.search(ql):
        return "decisions"
    if _RE_STATUS.search(ql):
        return "status"
    return "general"


def _terms(text: str) -> set[str]:
    """Lowercased alnum tokens, minus stopwords and very short tokens."""
    return {
        t for t in _TOKEN_RE.findall((text or "").lower())
        if len(t) >= _MIN_TERM_LEN and t not in _STOPWORDS
    }


@dataclass
class AskResult:
    """Deterministic package-local answer. ``answered`` is False iff no package
    evidence grounds the query (identical to the sensitive/unknown case). ``intent``
    and ``message`` describe the shape; the endpoint uses ``message`` on the
    answered path and a constant neutral message otherwise."""
    answered: bool
    claims: list  # matching HandoffClaim rows (grounded), capped
    evidence: list  # matching + cited HandoffEvidence rows (the citations), capped
    intent: str = "general"
    message: str = ""


def _label_scope(claims: list, q: set[str]) -> str | None:
    """If the query names an S39 project label (best term overlap, >=1 term), return
    that label so candidate claims can be scoped to the named project. None when the
    query names no project (then the whole package is searched)."""
    best_label, best_overlap = None, 0
    seen: set[str] = set()
    for c in claims:
        lbl = (getattr(c, "project_label", None) or "").strip()
        if not lbl or lbl in seen:
            continue
        seen.add(lbl)
        overlap = len(_terms(lbl) & q)
        if overlap > best_overlap:
            best_overlap, best_label = overlap, lbl
    return best_label if best_overlap >= 1 else None


def _is_blocker(c) -> bool:
    return c.kind == _KIND_BLOCKER or (
        c.kind == _KIND_OPEN_LOOP and bool(_RE_BLOCKER_TEXT.search(c.text or ""))
    )


def _shape_by_intent(intent: str, ranked: list) -> list:
    """Filter the relevance-ranked, in-scope claims to the requested shape.

    ``status``/``general`` keep everything (overall state); the specific intents
    keep only their kind, so status vs next-steps produce different answers."""
    if intent == "next_steps":
        return [c for c in ranked if c.kind == _KIND_OPEN_LOOP]
    if intent == "decisions":
        return [c for c in ranked if c.kind == _KIND_DECISION]
    if intent == "blocked":
        return [c for c in ranked if _is_blocker(c)]
    return ranked  # status / general -> overall


# Intent -> (message when items exist, message when none exist). The label suffix
# is appended by the caller. These are safe because we only reach them once the
# topic already matched visible package rows.
_MSG_WITH = {
    "next_steps": "Open next steps{on}:",
    "blocked": "Blockers and open risks{on}:",
    "decisions": "Decisions and outcomes{on}:",
    "status": "Here's where things stand{on}:",
    "general": "Here's what this handoff package covers on that:",
}
_MSG_NONE = {
    "next_steps": "No explicit next steps were found{on} in this package.",
    "blocked": "No blockers were found{on} in this package.",
    "decisions": "No decisions were recorded{on} in this package.",
    # status/general never hit the "none" path (they keep everything).
}


def _message(intent: str, has_items: bool, label: str | None) -> str:
    on = f" for {label}" if label else ""
    table = _MSG_WITH if has_items else _MSG_NONE
    template = table.get(intent) or _MSG_WITH["general"]
    return template.format(on=on)


def answer_from_package(query: str, claims: list, evidence: list) -> AskResult:
    """Answer ``query`` against the package's own claims/evidence, shaped by intent.

    Pipeline: (1) rank claims/evidence by term overlap; (2) if nothing matches,
    return the neutral no-answer (oracle-safe, intent-independent); (3) scope to a
    named project when the query names one; (4) filter to the intent's shape;
    (5) return the shaped claims + their in-package citations, capped. Every
    returned evidence row is an in-package ``HandoffEvidence`` and every returned
    claim an in-package ``HandoffClaim``.
    """
    q = _terms(query)
    intent = detect_ask_intent(query)
    if not q:
        return AskResult(False, [], [], intent=intent)

    by_header = {e.message_id_header: e for e in evidence}

    def claim_score(c) -> int:
        # S39: the frozen project_label is package-local visible text and a valid
        # match signal, so a claim whose text does not repeat its project name still
        # matches a query that names that project.
        pl = getattr(c, "project_label", None) or ""
        return len(q & (_terms(c.text) | _terms(pl)))

    # Detect a named project across ALL visible claims via the frozen label (S39).
    # A label exists only on surviving, non-excluded claims, so this is snapshot-safe
    # and can never surface an excluded project's label.
    label = _label_scope(claims, q)

    if label:
        # Scope candidates to the named project (regardless of whether each claim's
        # own text repeats the label); order by in-project relevance.
        candidates = [
            c for c in claims
            if (getattr(c, "project_label", None) or "").strip() == label
        ]
        candidates.sort(key=claim_score, reverse=True)
    else:
        cl_scored = sorted(((claim_score(c), c) for c in claims),
                           key=lambda p: p[0], reverse=True)
        candidates = [c for s, c in cl_scored if s > 0]

    # Rank evidence by term overlap over its safe, package-local fields only
    # (grounding + the general evidence-only fallback).
    ev_scored = []
    for e in evidence:
        s = len(q & _terms(" ".join([
            e.subject or "", e.body_snapshot or "",
            e.sender_display or "", e.sender_domain or "",
        ])))
        if s > 0:
            ev_scored.append((s, e))
    ev_scored.sort(key=lambda p: (p[0], p[1].ts or _TS_MIN), reverse=True)
    ev_matched = [e for _, e in ev_scored]

    # Oracle safety: the query names no visible project AND matches no claim/evidence
    # -> neutral no-answer, BEFORE any shaping, so a sensitive/unknown topic is
    # indistinguishable from a miss. A matched project label is itself a legitimate
    # visible match (it only exists on surviving, non-excluded claims).
    if not label and not candidates and not ev_matched:
        return AskResult(False, [], [], intent=intent)

    shaped = _shape_by_intent(intent, candidates)

    # Specific intent, topic matched but no claim of that shape: answer True with an
    # honest "none found" - never restate other work (e.g. completed) as the answer.
    if intent in ("next_steps", "blocked", "decisions") and not shaped:
        return AskResult(True, [], [], intent=intent,
                         message=_message(intent, False, label))

    # No claim matched at all (only evidence did): legacy general evidence answer.
    if not shaped:
        selected = ev_matched[:_MAX_EVIDENCE]
        return AskResult(True, [], selected, intent=intent,
                         message=_message("general", True, None))

    shaped = shaped[:_MAX_CLAIMS]

    # Evidence = the shaped claims' in-package citations, in package order, capped -
    # so every returned evidence row sits under a claim it supports (no orphan wall).
    ev_order = {e.message_id_header: i for i, e in enumerate(evidence)}
    header_set: list[str] = []
    for c in shaped:
        for h in c.source_message_id_headers:
            if h in by_header and h not in header_set:
                header_set.append(h)
    header_set.sort(key=lambda h: ev_order.get(h, 0))
    header_set = header_set[:_MAX_EVIDENCE]
    selected_evidence = [by_header[h] for h in header_set]

    # Grounding rule: no package evidence -> no answer (should not happen here since
    # generated claims always cite in-package evidence, but keep the invariant).
    if not selected_evidence:
        return AskResult(False, [], [], intent=intent)

    # Answer-local citation rule: keep only claims that still cite a returned row.
    returned = {e.message_id_header for e in selected_evidence}
    grounded = [c for c in shaped if any(h in returned for h in c.source_message_id_headers)]
    return AskResult(True, grounded, selected_evidence, intent=intent,
                     message=_message(intent, True, label))
