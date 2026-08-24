"""Format handoff-eval results for the console or JSON (S43). Pure formatting."""
from __future__ import annotations

from services.handoff.eval.harness import ScenarioResult


def to_json(r: ScenarioResult) -> dict:
    return {
        "name": r.name,
        "hard_pass": r.hard_pass,
        "hard_gates": r.hard_gates,
        "quality": r.quality,
        "limitations": r.limitations,
        "counts": r.counts,
    }


def _gate_line(name: str, ok: bool) -> str:
    return f"    [{'PASS' if ok else 'FAIL'}] {name}"


def format_console(results: list[ScenarioResult]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("  HANDOFF QUALITY EVAL (S43)")
    lines.append("=" * 72)
    for r in results:
        q = r.quality
        lines.append("")
        lines.append(f"SCENARIO: {r.name}   [{'PASS' if r.hard_pass else 'FAIL'} hard gates]")
        lines.append(f"  counts: {r.counts['claims']} claims / {r.counts['evidence']} evidence "
                     f"/ {r.counts['exclusions']} exclusions")
        lines.append("  hard gates:")
        for name, ok in r.hard_gates.items():
            lines.append(_gate_line(name, ok))
        lines.append("  quality:")
        lines.append(f"    decisions   {q['decisions_found']}/{q['decisions_expected']} found"
                     + (f"  MISSING: {q['missing_decisions']}" if q["missing_decisions"] else ""))
        lines.append(f"    open loops  {q['open_loops_found']}/{q['open_loops_expected']} found"
                     + (f"  MISSING: {q['missing_open_loops']}" if q["missing_open_loops"] else ""))
        lines.append(f"    blocker content  {q['blocker_content_found']}/{q['blocker_content_expected']} found"
                     + (f"  MISSING: {q['missing_blocker_content']}" if q["missing_blocker_content"] else ""))
        lines.append(f"    blocker kind present: {q['blocker_kind_present']}")
        lines.append(f"    project labels present: {q['project_labels_present']}"
                     + (f"  MISSING: {q['missing_project_labels']}" if q["missing_project_labels"] else ""))
        lines.append(f"    stakeholders present:   {q['stakeholders_present']}"
                     + (f"  MISSING: {q['missing_stakeholders']}" if q["missing_stakeholders"] else ""))
        lines.append(f"    claim precision proxy:  {q['claim_precision_proxy']}")
        if q["unexpected_claims"]:
            lines.append(f"    unexpected claims: {q['unexpected_claims']}")
        if r.limitations:
            lines.append("  known limitations (not a hard failure; candidate S44 work):")
            for lim in r.limitations:
                lines.append(f"    - {lim}")

    passed = sum(1 for r in results if r.hard_pass)
    all_lims = sorted({lim for r in results for lim in r.limitations})
    lines.append("")
    lines.append("-" * 72)
    lines.append(f"SUMMARY: {passed}/{len(results)} scenarios pass all hard gates.")
    if all_lims:
        lines.append("Known limitations across the corpus (candidate S44 privacy/safety-gate work):")
        for lim in all_lims:
            lines.append(f"  - {lim}")
    lines.append("=" * 72)
    return "\n".join(lines)
