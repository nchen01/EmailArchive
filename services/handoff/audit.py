"""Handoff audit trail (S17.3).

Append-only `handoff_audit_event` rows. **Safe metadata only**: counts, ids,
status — never message bodies, subjects, snippets, tokens, secrets, or raw
exception text (mirrors the S16 audit discipline and the S17.2 spec).
"""
from __future__ import annotations

from services.db import models as orm

# Metadata values are restricted to scalars and lists of scalars; a defensive
# guard drops anything else so a caller cannot accidentally log content.
_SCALAR = (str, int, float, bool, type(None))

# Keys whose name suggests message content, a secret, or error text are dropped
# regardless of value type — so a future caller cannot smuggle a body/subject/
# token into audit metadata just because it is a string (S17.2 invariant).
_BLOCKED_KEY_FRAGMENTS = (
    "body", "subject", "snippet", "clean_text", "raw", "mime", "token",
    "secret", "credential", "password", "api_key", "apikey", "exception",
    "traceback", "prompt", "response", "content",
)


def _safe_metadata(metadata: dict | None) -> dict:
    if not metadata:
        return {}
    out: dict = {}
    for k, v in metadata.items():
        key = str(k)
        if any(frag in key.lower() for frag in _BLOCKED_KEY_FRAGMENTS):
            continue  # content/secret/error-like key → never stored
        if isinstance(v, _SCALAR):
            out[key] = v
        elif isinstance(v, (list, tuple)) and all(isinstance(i, _SCALAR) for i in v):
            out[key] = list(v)
        # anything else (dicts, objects, bytes) is dropped, not stored
    return out


def write_handoff_audit(
    session,
    *,
    package_id: str,
    actor: str,
    action: str,
    lineage_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Append one immutable handoff audit row. Commits.

    ``actor`` is a role/subject string (e.g. ``owner:<email>``), never a raw
    token. ``metadata`` is sanitized to scalars/lists of scalars.
    """
    session.add(orm.HandoffAuditEvent(
        package_id=package_id,
        lineage_id=lineage_id,
        actor=actor,
        action=action,
        metadata_=_safe_metadata(metadata),
    ))
    session.commit()
