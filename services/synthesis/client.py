"""Anthropic client wrapper for L3 synthesis (4.6).

Holds: the API-key interface (D6 — env var, never DB/logs), and a structured
synthesis call that puts ``cache_control`` on the stable context block so repeat
queries hit the prompt cache (verify via ``usage.cache_read_input_tokens``).

The network call is isolated here; the synthesis modules (4.7/4.8) inject a
``synth_fn`` so their tests stay offline. ``make_anthropic_synth_fn`` builds the
production ``synth_fn`` from this client.

Environment overrides (read at synth_fn construction time, after .env is loaded):
  ANTHROPIC_MODEL               override the model ID (default: params.model)
  ANTHROPIC_DISABLE_CACHE_CONTROL=1  remove cache_control from the context block
                                (use for debugging if the API rejects the field)
"""
from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)

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


# ── Tool input schema ─────────────────────────────────────────────────────────
#
# Written as a flat dict rather than derived from a Pydantic model because
# Pydantic v2's model_json_schema() emits "$defs"/"$ref" for nested models,
# and the Anthropic tools API does not support JSON Schema $ref in input_schema.
# Using $ref causes HTTP 400 "invalid_request_error".
#
_EMIT_SYNTHESIS_INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One factual clause, grounded in the provided messages.",
                    },
                    "source_message_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "message_id_header values that evidence this claim.",
                    },
                },
                "required": ["text", "source_message_ids"],
            },
        }
    },
}

# Keep the Pydantic model for response parsing only — it is never sent to the API.
from pydantic import BaseModel, Field as _Field


class _LLMClaim(BaseModel):
    text: str = _Field(..., description="One factual clause, grounded.")
    source_message_ids: list[str] = _Field(
        ..., description="message_id_header value(s) that evidence this claim."
    )


class _LLMSynthesis(BaseModel):
    claims: list[_LLMClaim] = _Field(default_factory=list)


def make_anthropic_synth_fn(
    params: SynthesisParams = PARAMS, *, api_key: str | None = None
):
    """Build the production ``synth_fn(system, context, query) -> SynthesisResult``.

    Reads ANTHROPIC_MODEL and ANTHROPIC_DISABLE_CACHE_CONTROL from the environment
    at construction time so .env overrides applied in main() are honoured.

    ``context`` is the large, stable block (project/contact digest). When
    ANTHROPIC_DISABLE_CACHE_CONTROL is not "1", it is tagged with cache_control so
    repeat queries hit the prompt cache.
    """
    model = os.environ.get("ANTHROPIC_MODEL") or params.model
    use_cache = os.environ.get("ANTHROPIC_DISABLE_CACHE_CONTROL", "").strip() != "1"

    client = get_anthropic_client(api_key=api_key, timeout_s=params.timeout_s)

    def synth_fn(system: str, context: str, query: str) -> SynthesisResult:
        context_block: dict = {"type": "text", "text": context}
        if use_cache:
            context_block["cache_control"] = {"type": "ephemeral"}

        try:
            resp = client.messages.create(
                model=model,
                max_tokens=params.max_tokens,
                system=[{"type": "text", "text": system}],
                messages=[
                    {
                        "role": "user",
                        "content": [context_block, {"type": "text", "text": query}],
                    }
                ],
                tools=[
                    {
                        "name": "emit_synthesis",
                        "description": "Emit the grounded, cited synthesis.",
                        "input_schema": _EMIT_SYNTHESIS_INPUT_SCHEMA,
                    }
                ],
                tool_choice={"type": "tool", "name": "emit_synthesis"},
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            # provider_msg is the API error message — safe to log because it
            # contains only the provider's rejection reason (e.g. "model not
            # found", "invalid field"), never our prompt/context/key values.
            provider_msg = getattr(exc, "message", None) or str(exc)
            _log.error(
                "Anthropic API call failed (%s%s): %s",
                type(exc).__name__,
                f" status={status}" if status else "",
                provider_msg,
            )
            raise

        claims: list[SynthesisClaim] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            parsed = _LLMSynthesis.model_validate(block.input)
            for c in parsed.claims:
                if not c.source_message_ids or not c.text.strip():
                    continue
                claims.append(
                    SynthesisClaim(
                        text=c.text, source_message_ids=list(c.source_message_ids)
                    )
                )

        usage = _usage_dict(resp.usage)
        return SynthesisResult(claims=claims, model=model, usage=usage)

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
