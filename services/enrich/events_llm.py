"""Production ``extract_fn`` — Anthropic structured output (S4 ticket 4.2).

This is the *only* place in Track A that touches the network. It is isolated so
``events.py`` (4.1) tests never import it (no anthropic/instructor required to
run the offline suite). The returned callable matches the ``ExtractFn`` contract
that ``extract_events`` expects.

Structured output is enforced with a Pydantic tool schema (``instructor``), not
freeform text + regex (spec 01 §7, S4 guide §4). The large, stable system prompt
(the epistemic decision table) is marked with ``cache_control`` so repeated
extraction runs hit the prompt cache.
"""
from __future__ import annotations

import os

from pydantic import BaseModel, Field

from .events import ExtractedEventRaw, ThreadContext

# Default model — read from config in production; this is the fallback id (D10).
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
REQUEST_TIMEOUT_S = 60.0


# ── structured-output schema (what the model must return) ─────────────────────

class _LLMEvent(BaseModel):
    actor: str = Field(
        ..., description="Name or email of the person who took/proposed the action."
    )
    type: str = Field(
        ..., description="One of: proposed, did, outcome."
    )
    summary: str = Field(
        ..., description="One factual clause, no adjectives."
    )
    source_message_ids: list[str] = Field(
        ...,
        description="message_id_header value(s) from the thread that evidence this event.",
    )


class _LLMEventList(BaseModel):
    events: list[_LLMEvent] = Field(default_factory=list)


# ── prompts (spec 01 §7) ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You extract events from a single email thread for an honest accomplishment record.

Extract events as a JSON list. Each: {actor, type, summary, source_message_ids}.

type is one of:
  proposed (future/intent), did (action taken),
  outcome (confirmed result with evidence in the text).

Epistemic decision table — apply it strictly:
  - Future tense / intent / commitment        -> proposed
    ("I'll re-shard the cluster tonight", "We should send the contract")
  - Past-tense action, no confirmed result    -> did
    ("Pushed the migration branch", "Sent the SOW to procurement")
  - Confirmed result, evidenced in the text   -> outcome
    ("Staging cutover completed and verified", "Contract signed, copy attached")

Hard rules:
  - Do NOT infer outcome from message volume or tone. Volume is not accomplishment.
  - If no outcome is stated, do not emit one.
  - summary: one factual clause, no adjectives.
  - Every event MUST include source_message_ids: the message_id values from the
    thread that evidence it. An event with no citation is invalid — omit it.
"""

QUERY_PROMPT = """\
Extract the events from the thread above. Return only events you can ground in
the message text, each citing the message_id(s) it is drawn from."""


def _render_thread(ctx: ThreadContext) -> str:
    lines = [f"Thread subject: {ctx.subject}", ""]
    for m in ctx.messages:
        lines.append(f"message_id: {m.message_id_header}")
        lines.append(f"from: {m.sender_email}")
        lines.append(f"date: {m.ts}")
        lines.append(f"body: {m.clean_text}")
        lines.append("")
    return "\n".join(lines)


# ── public factory ────────────────────────────────────────────────────────────

def get_api_key() -> str:
    """Read the Anthropic key through an interface (D6); never DB/logs."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    return key


def make_anthropic_extract_fn(
    model: str = DEFAULT_MODEL,
    *,
    api_key: str | None = None,
    max_tokens: int = MAX_TOKENS,
    timeout_s: float = REQUEST_TIMEOUT_S,
):
    """Build the production ``extract_fn`` backed by Anthropic structured output.

    Lazily imports anthropic/instructor so the offline test suite (which never
    calls this) does not require them at import time.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key or get_api_key(), timeout=timeout_s)

    def extract_fn(ctx: ThreadContext) -> list[ExtractedEventRaw]:
        thread_block = _render_thread(ctx)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # Cache the stable instruction block across thread calls.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": thread_block},
                        {"type": "text", "text": QUERY_PROMPT},
                    ],
                }
            ],
            tools=[
                {
                    "name": "emit_events",
                    "description": "Emit the extracted events for this thread.",
                    "input_schema": _LLMEventList.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": "emit_events"},
        )

        raws: list[ExtractedEventRaw] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            parsed = _LLMEventList.model_validate(block.input)
            for ev in parsed.events:
                raws.append(
                    ExtractedEventRaw(
                        actor_name_or_email=ev.actor,
                        type=ev.type,
                        summary=ev.summary,
                        source_message_id_headers=list(ev.source_message_ids),
                    )
                )
        return raws

    return extract_fn
