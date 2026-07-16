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


def _safe_metadata(metadata: dict | None) -> dict:
    if not metadata:
        return {}
    out: dict = {}
    for k, v in metadata.items():
        if isinstance(v, _SCALAR):
            out[str(k)] = v
        elif isinstance(v, (list, tuple)) and all(isinstance(i, _SCALAR) for i in v):
            out[str(k)] = list(v)
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
