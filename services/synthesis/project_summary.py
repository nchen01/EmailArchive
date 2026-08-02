"""L3 "What's been done" project synthesis (S4 ticket 4.7, spec 02 §6).

Assembles grounded context from L1 objects (Events + recent threads). In S7
this will be upgraded to also accept L2 retrieval hits as supporting evidence;
for now it operates on L1 only. The model call is
injected as ``synth_fn`` so tests stay offline and deterministic.

Anti-patterns enforced in the system prompt (S4 guide §5):
- Volume != accomplishment.
- Proposed != outcome (carry the epistemic label; never upgrade).
- Partial-record framing (email is one channel).
- No confident language when evidence is indirect.
"""
from __future__ import annotations

from typing import Callable

from ekc_schemas import Event, Message, Project, Thread

from .client import make_anthropic_synth_fn
from .contracts import SynthesisResult
from .params import PARAMS, SynthesisParams

SynthFn = Callable[[str, str, str], SynthesisResult]

# Epistemic grade order for grouping (outcome > did > proposed).
_GRADE_ORDER = ["outcome", "did", "proposed"]

SYSTEM_PROMPT = """\
You write an honest, grounded summary of what has been done on a project, drawn
ONLY from the email evidence provided. This is a partial record: email is one
channel; real work also lives in Slack, docs, and meetings. Surface what is
evidenced; flag what cannot be seen from email.

Rules — every one is mandatory:
  - Every claim MUST cite the message_id_header value(s) from the provided messages
    that evidence it. No citation, no claim. Cite only message_id values.
  - Carry the epistemic label through. A 'proposed' item is intent, not a result;
    never present it as an outcome. Never upgrade proposed/did into outcome.
  - Volume is not accomplishment. Never render "sent N emails" as "drove to completion".
  - Prefer "coordinated across N threads; final outcome not visible in email" over a
    confident fabrication when evidence is indirect.
  - One factual clause per claim, no adjectives.
"""

QUERY = (
    "Summarize what has been done on this project. Every claim must cite the "
    "message_id_header value(s) from the provided messages that evidence it."
)


def _group_events(events: list[Event]) -> dict[str, list[Event]]:
    grouped: dict[str, list[Event]] = {g: [] for g in _GRADE_ORDER}
    for e in events:
        grouped.setdefault(e.type.value, []).append(e)
    return grouped


def build_context(
    project: Project,
    events: list[Event],
    threads: list[Thread],
    messages_by_thread: dict[str, list[Message]],
    params: SynthesisParams,
) -> str:
    """Stable context block (the cached portion): project label + Events grouped
    by epistemic grade + recent thread subjects/participants."""
    lines = [f"Project: {project.label}", ""]

    grouped = _group_events(events)
    lines.append("Events by epistemic grade (outcome > did > proposed):")
    for grade in _GRADE_ORDER:
        bucket = grouped.get(grade) or []
        if not bucket:
            continue
        lines.append(f"  [{grade}]")
        for e in bucket:
            cites = ", ".join(e.source_message_ids)
            lines.append(f"    - {e.summary} (cite: {cites})")
    if not events:
        lines.append("  (no events extracted from email)")
    lines.append("")

    # Recent threads, capped by context size.
    recent = sorted(threads, key=lambda t: t.t_end, reverse=True)[
        : params.max_context_messages
    ]
    lines.append("Recent threads:")
    for t in recent:
        parts = ", ".join(t.participants)
        lines.append(f"  - {t.subject_norm} (participants: {parts}; last: {t.t_end.isoformat()})")
    return "\n".join(lines)


def synthesize_project(
    project: Project,
    events: list[Event],
    threads: list[Thread],
    messages_by_thread: dict[str, list[Message]],
    *,
    synth_fn: SynthFn | None = None,
    params: SynthesisParams = PARAMS,
    allowed_message_id_headers: set[str] | None = None,
    query: str | None = None,
) -> SynthesisResult:
    """Synthesize "What's been done" for a project (spec 02 §6).

    With no events AND no threads, returns an empty/"no evidenced activity"
    result rather than calling the model (graceful empty path, DoD §10).

    ``allowed_message_id_headers``: when supplied, claims whose citations are
    not within this set are dropped before the result leaves the synthesis layer.

    ``query``: the model user-turn text. Defaults to the fixed module ``QUERY``
    (a generic "summarize" prompt) so existing callers are unchanged; Cover-for-me
    passes an intent-shaped question so the answer addresses what was asked.
    """
    if not events and not threads:
        return SynthesisResult(
            claims=[],
            model=params.model,
            usage={},
            state="no evidenced activity in email",
        )

    if synth_fn is None:
        synth_fn = make_anthropic_synth_fn(params)

    context = build_context(project, events, threads, messages_by_thread, params)
    result = synth_fn(SYSTEM_PROMPT, context, query or QUERY)

    if allowed_message_id_headers is not None:
        result = result.model_copy(update={
            "claims": [
                c for c in result.claims
                if all(h in allowed_message_id_headers for h in c.source_message_ids)
            ]
        })
    return result
