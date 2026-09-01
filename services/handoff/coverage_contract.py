"""Per-project coverage contract assembly (S48, spec docs/s47-project-coverage-contract-plan.md).

Computed-only MVP: the coverage contract is a PURE function of a package's already
frozen claims + evidence. It groups claims by the S39 frozen ``project_label`` and,
per project, states what the handoff covers (a templated summary), the settled
decisions, the open loops / next actions, the blockers, optional people notes, the
cited in-package evidence, a neutral boundary statement, and a neutral safety
posture.

Design invariants (see the S47 spec sections 6 + 8):
  - No new storage, no migration, no live table access. Input is frozen rows only.
  - Every contract ITEM is citation-backed: a claim only becomes an item if it
    cites >= 1 in-package evidence header (claims citing nothing in-package are
    dropped, so the citation-backed guarantee holds by construction).
  - Anti-oracle: the contract carries NO per-project exclusion counts and NO
    hidden-content categories. "What it does not cover" is expressed only as a
    neutral boundary statement here (an explicit creator-declared out-of-scope
    label is the deferred Option C increment, not part of this MVP).
  - Determinism: output is a pure function of the ordered frozen rows.

The module is deliberately DB-free and pydantic-free so it is trivially unit
testable and reusable by the S43 eval harness. Callers map the returned plain
dicts into their DTOs.

Deferred from S48 (see docs/s47 "S48 implementation status"): the
``coverage_contract_confirmed`` creator acknowledgement is NOT implemented here.
S48 is read-only/computed-only; the confirmation stays a future, optional,
safe-metadata-only (project_count + per-kind totals), NO-migration audit write on
the existing publish path.
"""
from __future__ import annotations

from typing import Any

# Fallback group labels, mirroring the frontend S37/S39 grouping semantics.
UNASSIGNED_LABEL = "Unassigned / cross-project"
OTHER_EVIDENCE_LABEL = "Other evidence (not cited by a claim)"

# Claim kind -> contract bucket. project_state is folded into open loops (framed as
# current status / next). Unmapped kinds (e.g. briefing) still contribute their
# cited evidence to ``evidence_refs`` but are not rendered as a typed item.
_DECISION_KINDS = {"decision"}
_OPEN_LOOP_KINDS = {"open_loop", "project_state"}
_BLOCKER_KINDS = {"blocker"}
_PEOPLE_KINDS = {"person_note"}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _covers_summary(label: str, counts: dict[str, int], *, return_mode: bool, is_other: bool) -> str:
    if is_other:
        return "Evidence included for context, not cited by a specific claim."
    parts: list[str] = []
    if counts["decisions"]:
        parts.append(_plural(counts["decisions"], "decision"))
    if counts["open_loops"]:
        parts.append(_plural(counts["open_loops"], "open loop"))
    if counts["blockers"]:
        parts.append(_plural(counts["blockers"], "blocker"))
    if counts["people"]:
        parts.append(_plural(counts["people"], "person note"))
    body = ", ".join(parts) if parts else "no itemized claims"
    if return_mode:
        return f"What changed in {label} while you were away: {body}."
    return f"Covers {label}: {body}."


def _boundary(label: str, *, return_mode: bool, is_fallback: bool, is_other: bool) -> str:
    if is_other:
        return (
            "This is supporting evidence not tied to a specific project claim. "
            "Coverage is limited to the items listed in this package."
        )
    if is_fallback:
        return (
            "Cross-project or unassigned items. Coverage is limited to the items "
            "listed in this package."
        )
    if return_mode:
        return (
            f"Covers changes in {label} during the coverage period. Coverage is "
            "limited to the items listed here; anything not listed was not part of "
            "this handoff."
        )
    return (
        f"This handoff covers {label}. Coverage is limited to the items listed "
        "here; anything not listed was not part of this handoff."
    )


def _item(claim: dict[str, Any], in_package_cites: list[str]) -> dict[str, Any]:
    return {
        "claim_id": claim["id"],
        "kind": claim["kind"],
        "text": claim["text"],
        "source_message_id_headers": in_package_cites,
    }


def build_coverage_contract(
    claims: list[dict[str, Any]],
    evidence_headers: list[str],
    *,
    return_mode: bool = False,
) -> list[dict[str, Any]]:
    """Assemble the per-project coverage contract from frozen rows.

    ``claims`` are normalized dicts with keys ``id``, ``kind``, ``text``,
    ``project_label`` (may be None/empty), and ``cites`` (message-id headers).
    ``evidence_headers`` is the ordered list of in-package evidence headers.

    Returns an ordered list of entry dicts (named projects A-Z, then the
    unassigned fallback, then the other-evidence bucket last). Safe metadata only.
    """
    header_order = {h: i for i, h in enumerate(evidence_headers)}
    header_set = set(evidence_headers)

    # Group claims by frozen label; only claims that cite in-package evidence are
    # eligible (citation-backed guarantee). Preserve which headers are cited.
    groups: dict[str, list[dict[str, Any]]] = {}
    group_order: list[str] = []
    cited_headers: set[str] = set()
    for c in claims:
        in_pkg = [h for h in c.get("cites", []) if h in header_set]
        if not in_pkg:
            continue  # not citation-backed by this package -> excluded from the contract
        label = (c.get("project_label") or "").strip()
        key = label if label else UNASSIGNED_LABEL
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append({**c, "_in_pkg_cites": in_pkg})
        cited_headers.update(in_pkg)

    entries: list[dict[str, Any]] = []
    for key in group_order:
        gclaims = groups[key]
        is_fallback = key == UNASSIGNED_LABEL
        decisions, open_loops, blockers, people = [], [], [], []
        ev_refs: set[str] = set()
        for c in gclaims:
            in_pkg = c["_in_pkg_cites"]
            ev_refs.update(in_pkg)
            item = _item(c, in_pkg)
            if c["kind"] in _DECISION_KINDS:
                decisions.append(item)
            elif c["kind"] in _OPEN_LOOP_KINDS:
                open_loops.append(item)
            elif c["kind"] in _BLOCKER_KINDS:
                blockers.append(item)
            elif c["kind"] in _PEOPLE_KINDS:
                people.append(item)
            # unmapped kinds (e.g. briefing): evidence still counted in ev_refs
        counts = {
            "decisions": len(decisions),
            "open_loops": len(open_loops),
            "blockers": len(blockers),
            "people": len(people),
        }
        evidence_refs = sorted(ev_refs, key=lambda h: header_order.get(h, 0))
        entries.append({
            "project_label": key,
            "is_fallback": is_fallback,
            "covers_summary": _covers_summary(key, counts, return_mode=return_mode, is_other=False),
            "decisions": decisions,
            "open_loops": open_loops,
            "blockers": blockers,
            "people": people,
            "evidence_refs": evidence_refs,
            "boundary": _boundary(key, return_mode=return_mode, is_fallback=is_fallback, is_other=False),
            "safety_posture": {"scope_limited": True, "sensitive_excluded": True},
        })

    # Named projects A-Z, then the unassigned fallback.
    entries.sort(key=lambda e: (e["is_fallback"], e["project_label"].lower()))

    # Other-evidence bucket: package evidence cited by no contract claim. Always
    # last, so the visible evidence set reconciles with the package evidence set.
    ungrouped = [h for h in evidence_headers if h not in cited_headers]
    if ungrouped:
        entries.append({
            "project_label": OTHER_EVIDENCE_LABEL,
            "is_fallback": True,
            "covers_summary": _covers_summary(OTHER_EVIDENCE_LABEL, {}, return_mode=return_mode, is_other=True),
            "decisions": [],
            "open_loops": [],
            "blockers": [],
            "people": [],
            "evidence_refs": ungrouped,
            "boundary": _boundary(OTHER_EVIDENCE_LABEL, return_mode=return_mode, is_fallback=True, is_other=True),
            "safety_posture": {"scope_limited": True, "sensitive_excluded": True},
        })

    return entries


def contract_from_orm(claims: list[Any], evidence: list[Any], *, return_mode: bool = False) -> list[dict[str, Any]]:
    """Convenience adapter: build the contract straight from ORM HandoffClaim /
    HandoffEvidence rows (the shape both API serializers already hold)."""
    norm = [{
        "id": c.id,
        "kind": c.kind,
        "text": c.text,
        "project_label": c.project_label,
        "cites": list(c.source_message_id_headers),
    } for c in claims]
    headers = [e.message_id_header for e in evidence]
    return build_coverage_contract(norm, headers, return_mode=return_mode)
