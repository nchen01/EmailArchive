"""L3 "Ask about this contact" synthesis (S4 ticket 4.8, spec 05 §3.4).

Context (S4 guide §5, §11 #4): Person name/role + Edge stats + shared thread
subjects + capped recent Events from those threads. Edge stats and thread
subjects give structural context even when no Events exist; Events (already
grounded) improve answer quality. The model call is injected as ``synth_fn``.
"""
from __future__ import annotations

from typing import Callable

from ekc_schemas import Edge, Event, Person, Thread

from .client import make_anthropic_synth_fn
from .contracts import SynthesisResult
from .params import PARAMS, SynthesisParams

SynthFn = Callable[[str, str, str], SynthesisResult]

SYSTEM_PROMPT = """\
You write an honest, grounded summary of the mailbox owner's working relationship
with one contact, drawn ONLY from the email evidence provided. This is a partial
record: email is one channel; real collaboration also lives in Slack, docs, and
meetings. Surface what is evidenced; flag what cannot be seen from email.

Rules — every one is mandatory:
  - Every claim MUST cite the message_id_header value(s) from the provided
    messages that evidence it. No citation, no claim. Cite only message_id values
    — not thread IDs or any other identifier.
  - Volume is not accomplishment. Many messages is not evidence of a result.
  - Carry epistemic labels through; never upgrade intent into a confirmed outcome.
  - Prefer "coordinated across N threads; outcome not visible in email" over a
    confident fabrication when evidence is indirect.
  - One factual clause per claim, no adjectives.
"""

QUERY = (
    "Summarize what this contact works on with the owner and in what capacity. "
    "Every claim must cite the message_id_header value(s) from the provided "
    "messages that evidence it."
)


def _person_name(person: Person) -> str:
    return person.names[0] if person.names else person.canonical_email


def build_context(
    person: Person,
    edge: Edge,
    threads: list[Thread],
    events: list[Event],
    params: SynthesisParams,
) -> str:
    lines = [
        f"Contact: {_person_name(person)} <{person.canonical_email}>",
        f"Role: {person.role.value} (confidence {person.role_confidence:.2f})",
        "",
        "Relationship (Edge) stats:",
        f"  messages: {edge.message_count}",
        f"  owner->contact: {edge.sent_to_count}",
        f"  contact->owner: {edge.received_count}",
        f"  first contact: {edge.first_contact.isoformat()}",
        f"  last contact: {edge.last_contact.isoformat()}",
        "",
    ]

    recent_threads = sorted(threads, key=lambda t: t.t_end, reverse=True)[
        : params.max_context_messages
    ]
    lines.append("Shared threads:")
    if recent_threads:
        for t in recent_threads:
            lines.append(f"  - {t.subject_norm} (last: {t.t_end.isoformat()})")
    else:
        lines.append("  (none)")
    lines.append("")

    # Events arrive pre-sorted by recency (synthesis endpoint orders them);
    # just cap to max_context_messages here.
    capped_events = events[: params.max_context_messages]
    lines.append("Recent events from these threads:")
    if capped_events:
        for e in capped_events:
            cites = ", ".join(e.source_message_ids)
            lines.append(f"  - [{e.type.value}] {e.summary} (cite: {cites})")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def synthesize_contact(
    person: Person,
    edge: Edge,
    threads: list[Thread],
    events: list[Event],
    *,
    synth_fn: SynthFn | None = None,
    params: SynthesisParams = PARAMS,
    allowed_message_id_headers: set[str] | None = None,
) -> SynthesisResult:
    """Synthesize "Ask about this contact" (spec 05 §3.4).

    Edge stats always provide structural context, so this calls the model even
    with no events (unlike the empty-project short-circuit).

    ``allowed_message_id_headers``: when supplied, any claim whose
    ``source_message_ids`` are not all within this set is silently dropped
    before the result leaves the synthesis layer (citation hygiene).
    """
    if synth_fn is None:
        synth_fn = make_anthropic_synth_fn(params)

    context = build_context(person, edge, threads, events, params)
    result = synth_fn(SYSTEM_PROMPT, context, QUERY)

    if allowed_message_id_headers is not None:
        result = result.model_copy(update={
            "claims": [
                c for c in result.claims
                if all(h in allowed_message_id_headers for h in c.source_message_ids)
            ]
        })
    return result
