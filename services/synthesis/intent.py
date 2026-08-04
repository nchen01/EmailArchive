"""Question-intent shaping for Cover-for-me L1 synthesis (S35 follow-up C).

The L1 project/contact synthesis paths historically asked the model a *fixed*
question ("Summarize what has been done on this project"), discarding the user's
actual query. This module turns the user's real question into the model's
user-turn text and shapes the requested answer format by detected intent, so
"What is blocked for X?" yields a blockers list rather than a generic summary.

Pure and deterministic — no DB, no model, no I/O — so it is trivially testable
and cannot leak content. Citation discipline is unchanged: the shaped query
still demands a citation per claim, and the caller's allow-list filter still
drops any claim whose citations fall outside the permitted headers.
"""
from __future__ import annotations

import re

# Intent → the answer-shape instruction appended to the user's question. Order of
# evaluation matters (see detect_intent): the more specific intents are checked
# before the general "status"/"summary" fallbacks.
_BLOCKED = re.compile(r"\b(blocked|blocking|blocker|blockers|stuck|stalled|held\s+up|waiting\s+on)\b", re.I)
_NEXT = re.compile(
    r"\b(next\s+steps?|what\s+remains|what'?s\s+left|remaining|what\s+should\s+i\s+do|"
    r"to-?dos?|action\s+items?|follow[-\s]?ups?)\b",
    re.I,
)
_CHANGED = re.compile(r"\b(changed|changes|what'?s\s+new|new\s+developments?|updates?)\b", re.I)
_STATUS = re.compile(r"\b(status|state|where\s+(are|do)\s+(we|things)|progress|standing)\b", re.I)

# Timing buckets a next-step claim may be tagged with (S35 follow-up: temporal
# grouping). The model prefixes each next-step claim with EXACTLY one of these
# bracketed tags, and the Cover-for-me frontend (CoverForMe.tsx —
# NEXT_STEP_BUCKETS) parses the leading tag to render Now/Upcoming/Later/undated
# groups. Keep these strings byte-for-byte in sync with the frontend parser.
NEXT_STEP_BUCKETS = (
    "[Now / this week]",
    "[Upcoming]",
    "[Later]",
    "[No explicit date found]",
)

_INSTRUCTIONS = {
    "blocked": (
        "List the specific blockers or blocked items — one blocker per claim. "
        "If nothing is evidenced as blocked, say that in a single claim."
    ),
    "next_steps": (
        "List the concrete next steps or open action items, one action per claim. "
        "Prefix EACH claim with exactly one timing tag in square brackets, chosen "
        "only from: [Now / this week], [Upcoming], [Later], [No explicit date found]. "
        "Assign a dated tag ONLY when the evidence explicitly states timing (a date, "
        "deadline, or phrase like 'this week' / 'next sprint'); never invent or guess "
        "a due date. If an item is more than a week out, use [Upcoming] or [Later], "
        "not [Now / this week]. If the evidence gives no explicit timing for an item, "
        "tag it [No explicit date found]. If the evidence shows only activity with no "
        "concrete next action, say that in a single [No explicit date found] claim."
    ),
    "changed": "Summarize what has recently changed — one change per claim.",
    "status": (
        "Give a concise status summary of where things stand — the fewest "
        "claims that capture the current state."
    ),
    "summary": (
        "Give a concise, evidence-backed answer to the question — one factual "
        "clause per claim."
    ),
}

_CITE_CLAUSE = (
    "Every claim MUST cite the message_id_header value(s) from the provided "
    "messages that evidence it. No citation, no claim."
)


def detect_intent(query: str) -> str:
    """Classify a Cover-for-me question into an answer-shape intent.

    Returns one of: 'blocked', 'next_steps', 'changed', 'status', 'summary'.
    Specific intents win over general ones so "what's blocked" is not swallowed
    by a stray "status".
    """
    q = query or ""
    if _BLOCKED.search(q):
        return "blocked"
    if _NEXT.search(q):
        return "next_steps"
    if _CHANGED.search(q):
        return "changed"
    if _STATUS.search(q):
        return "status"
    return "summary"


def shape_query(user_query: str, fallback: str) -> str:
    """Build the model user-turn from the user's real question + intent shaping.

    ``fallback`` is the module's original fixed QUERY; if the user's question is
    blank we return it unchanged so behavior degrades to the prior default.
    """
    q = (user_query or "").strip()
    if not q:
        return fallback
    instruction = _INSTRUCTIONS[detect_intent(q)]
    return f'Answer this question directly using only the evidence: "{q}"\n{instruction}\n{_CITE_CLAUSE}'
