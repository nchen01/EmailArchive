"""Minimal Anthropic API diagnostic — isolates BadRequestError root cause.

Sends three progressively complex requests and prints only sanitized outcomes
(provider error type/message — never prompt text, context, or key values).

Usage:
    python scripts/diagnose_anthropic.py
    python scripts/diagnose_anthropic.py --model claude-3-5-sonnet-20241022
    python scripts/diagnose_anthropic.py --no-cache-control
    python scripts/diagnose_anthropic.py --no-tools

Steps:
  1. Minimal ping (no tools, no cache) — validates model ID and key.
  2. Tool call with flat schema — validates tool format.
  3. Tool call + cache_control on context block — validates caching support.

Each step that fails stops here so the output identifies the first broken layer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._env import load_local_env

load_local_env()

import os

_FLAT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_message_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["text", "source_message_ids"],
            },
        }
    },
}

_PING_SYSTEM = "You are a diagnostic assistant."
_PING_USER = "Reply with the single word: pong"


def _safe_provider_msg(exc: Exception) -> str:
    """Return the provider error message — safe because it comes from the API,
    not from our prompt, context, or key values."""
    return getattr(exc, "message", None) or str(exc)


def _run(args) -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ERROR: ANTHROPIC_API_KEY is not set. Add it to .env or export it.")
        sys.exit(1)

    model = args.model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    print(f"model        : {model}")
    print(f"tools        : {'yes' if not args.no_tools else 'no'}")
    print(f"cache_control: {'yes' if not args.no_cache_control else 'no'}")
    print()

    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic SDK not installed. Run: pip install anthropic")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=key, timeout=15.0)

    # ── Step 1: minimal ping — model + key only ───────────────────────────────
    print("Step 1: minimal ping (no tools, no cache_control) ...")
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=10,
            system=_PING_SYSTEM,
            messages=[{"role": "user", "content": _PING_USER}],
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
        print(f"  OK — response: {text!r}\n")
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        print(
            f"  FAIL ({type(exc).__name__}"
            f"{f' status={status}' if status else ''}): {_safe_provider_msg(exc)}"
        )
        print("\nStep 1 failed — model or key is invalid. Fix before retrying.")
        sys.exit(1)

    if args.no_tools:
        print("--no-tools: skipping steps 2 and 3.")
        sys.exit(0)

    # ── Step 2: tool call with flat schema (no cache_control) ─────────────────
    print("Step 2: tool call with flat schema (no cache_control) ...")
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=100,
            system=_PING_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": "Return one claim citing id 'test-msg-id'.",
                }
            ],
            tools=[
                {
                    "name": "emit_synthesis",
                    "description": "Emit the grounded, cited synthesis.",
                    "input_schema": _FLAT_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": "emit_synthesis"},
        )
        print(f"  OK — stop_reason={resp.stop_reason!r}\n")
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        print(
            f"  FAIL ({type(exc).__name__}"
            f"{f' status={status}' if status else ''}): {_safe_provider_msg(exc)}"
        )
        print("\nStep 2 failed — tool/schema format is invalid.")
        sys.exit(1)

    if args.no_cache_control:
        print("--no-cache-control: skipping step 3.")
        sys.exit(0)

    # ── Step 3: tool call + cache_control on context block ────────────────────
    print("Step 3: tool call + cache_control on context block ...")
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=100,
            system=[{"type": "text", "text": _PING_SYSTEM}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Context: one message. id=test-msg-id body=test.",
                            "cache_control": {"type": "ephemeral"},
                        },
                        {
                            "type": "text",
                            "text": "Return one claim citing id 'test-msg-id'.",
                        },
                    ],
                }
            ],
            tools=[
                {
                    "name": "emit_synthesis",
                    "description": "Emit the grounded, cited synthesis.",
                    "input_schema": _FLAT_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": "emit_synthesis"},
        )
        print(f"  OK — stop_reason={resp.stop_reason!r}\n")
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        print(
            f"  FAIL ({type(exc).__name__}"
            f"{f' status={status}' if status else ''}): {_safe_provider_msg(exc)}"
        )
        print(
            "\nStep 3 failed — cache_control is not supported for this model/API version."
            "\nAdd ANTHROPIC_DISABLE_CACHE_CONTROL=1 to .env and retry."
        )
        sys.exit(1)

    print("All steps passed. The synthesis request format is valid for this model.")


def main() -> None:
    p = argparse.ArgumentParser(description="Anthropic API diagnostic.")
    p.add_argument("--model", default=None, help="Override model ID (default: env/params).")
    p.add_argument(
        "--no-cache-control",
        action="store_true",
        help="Skip step 3 (cache_control test).",
    )
    p.add_argument(
        "--no-tools",
        action="store_true",
        help="Skip steps 2 and 3 (tool call tests).",
    )
    _run(p.parse_args())


if __name__ == "__main__":
    main()
