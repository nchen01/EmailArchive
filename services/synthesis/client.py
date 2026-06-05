"""Anthropic client wrapper for L3 synthesis (4.6).

Holds: the API-key interface (D6 — env var, never DB/logs), and a structured
synthesis call that puts ``cache_control`` on the stable context block so repeat
queries hit the prompt cache (verify via ``usage.cache_read_input_tokens``).

The network call is isolated here; the synthesis modules (4.7/4.8) inject a
``synth_fn`` so their tests stay offline. ``make_anthropic_synth_fn`` builds the
production ``synth_fn`` from this client.
"""
from __future__ import annotations

import os

from pydantic import BaseModel, Field

from .contracts import SynthesisClaim, SynthesisResult
from .params import PARAMS, SynthesisParams


class MissingApiKeyError(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is absent. API layer maps this to 503."""


def get_api_key() -> str:
    """Read the Anthropic key through an interface (D6); never DB/logs."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise MissingApiKeyError("ANTHROPIC_API_KEY is not configured.")
    return key


def get_anthropic_client(*, api_key: str | None = None, timeout_s: float | None = None):
    """Construct an Anthropic client. Lazily imports the SDK so the offline test
    suite (which injects a fake synth_fn) need not have it installed."""
    import anthropic

    return anthropic.Anthropic(
        api_key=api_key or get_api_key(),
        timeout=timeout_s if timeout_s is not None else PARAMS.timeout_s,
    )


# ── structured-output schema returned by the model ────────────────────────────

class _LLMClaim(BaseModel):
    text: str = Field(..., description="One factual clause, grounded.")
    source_message_ids: list[str] = Field(
        ..., description="message_id_header value(s) that evidence this claim."
    )


class _LLMSynthesis(BaseModel):
    claims: list[_LLMClaim] = Field(default_factory=list)


def make_anthropic_synth_fn(
    params: SynthesisParams = PARAMS, *, api_key: str | None = None
):
    """Build the production ``synth_fn(system, context, query) -> SynthesisResult``.

    ``context`` is the large, stable block (project/contact digest) and is marked
    with ``cache_control`` so repeat queries reduce token cost.
    """
    client = get_anthropic_client(api_key=api_key, timeout_s=params.timeout_s)

    def synth_fn(system: str, context: str, query: str) -> SynthesisResult:
        resp = client.messages.create(
            model=params.model,
            max_tokens=params.max_tokens,
            system=[{"type": "text", "text": system}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": context,
                            # Cache the stable context across repeat queries.
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": query},
                    ],
                }
            ],
            tools=[
                {
                    "name": "emit_synthesis",
                    "description": "Emit the grounded, cited synthesis.",
                    "input_schema": _LLMSynthesis.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": "emit_synthesis"},
        )

        claims: list[SynthesisClaim] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            parsed = _LLMSynthesis.model_validate(block.input)
            for c in parsed.claims:
                # The validator rejects empty citations; skip such claims here so
                # an uncited model claim never breaks the whole response.
                if not c.source_message_ids:
                    continue
                claims.append(
                    SynthesisClaim(
                        text=c.text, source_message_ids=list(c.source_message_ids)
                    )
                )

        usage = _usage_dict(resp.usage)
        return SynthesisResult(claims=claims, model=params.model, usage=usage)

    return synth_fn


def _usage_dict(usage) -> dict:
    """Extract token usage, including cache metrics, defensively."""
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    out: dict = {}
    for f in fields:
        v = getattr(usage, f, None)
        if v is not None:
            out[f] = v
    return out
