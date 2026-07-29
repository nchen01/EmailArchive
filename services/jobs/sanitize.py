"""Safe-metadata sanitizer for job params/progress/summary/errors (S24 / S21 §10, §13).

Jobs must never carry email bodies/subjects/snippets, OAuth codes/tokens, LLM
prompts/responses, raw provider responses, or stack traces. This drops keys that
look content- or secret-like and coerces values to safe scalars, so nothing
unsafe can land in a job row, an API response, an audit event, or logs — even if
a caller passes something it shouldn't.
"""
from __future__ import annotations

# Substrings that mark a key as content/secret-like → dropped entirely.
_UNSAFE_KEY_SUBSTRINGS = (
    "body", "subject", "snippet", "content", "text", "message_body",
    "token", "secret", "credential", "password", "api_key", "apikey",
    "code", "prompt", "response", "raw", "mime", "traceback", "exception",
    "stack", "email", "recipient",
)
_MAX_STR = 300
_MAX_LIST = 50


def _safe_scalar(v):
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return v[:_MAX_STR]
    return None


def sanitize_metadata(data: dict | None) -> dict:
    """Return a safe copy: drop unsafe keys, truncate strings, keep only scalars,
    safe lists, and (recursively) safe dicts."""
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for k, v in data.items():
        kl = str(k).lower()
        if any(sub in kl for sub in _UNSAFE_KEY_SUBSTRINGS):
            continue
        if isinstance(v, dict):
            out[k] = sanitize_metadata(v)
        elif isinstance(v, list):
            items = [s for s in (_safe_scalar(i) for i in v) if s is not None]
            out[k] = items[:_MAX_LIST]
        else:
            s = _safe_scalar(v)
            if s is not None:
                out[k] = s
    return out


def safe_summary(text: str | None) -> str | None:
    if not text:
        return None
    return str(text)[:_MAX_STR]
