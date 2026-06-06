"""S6.3 live quality eval — metrics from labeled sample reports.

Reads the CSV reports produced by live_quality_report.py and a
human-filled label CSV (S6.2 workflow). Enforces hard S6 checks and
reports soft metric targets without failing on them.

Usage:
    python scripts/eval_live_quality.py \\
        --report-dir .local/reports/live-quality \\
        --labels .local/labels/live-mailbox-labels.csv

Hard checks (non-zero exit + clear error):
  - No @ signs in generated report CSV values (proxy for raw email leak)
  - No malformed label values (unknown token in allowed-value columns)
  - Fewer than 30 definitively reviewed rows
  - Any sensitive false negative in the reviewed sample
  - Any undocumented project-relevant row falsely marked as noise

Soft targets (reported, never a failure):
  - Noise precision >= 0.85
  - Project-relevant false-noise rate <= 0.10
  - HR/legal/privileged sensitivity recall >= 0.90
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── constants ─────────────────────────────────────────────────────────────────

VALID_NOISE       = {"noise", "not_noise", "unsure", ""}
VALID_SENSITIVITY = {"none", "hr", "legal", "privileged", "personal", "unsure", ""}
VALID_PROJECT     = {"yes", "no", "unsure", ""}
SENSITIVE_CLASSES = ["hr", "legal", "privileged", "personal"]

MIN_LABELED_FAIL  = 30   # fail if fewer definitively reviewed rows
MIN_LABELED_WARN  = 100  # warn if fewer
LOW_CONF_HARD     = 10   # mark metric low-confidence (no value reported)
LOW_CONF_SOFT     = 20   # mark metric low-confidence (value reported with flag)

SOFT_TARGETS = {
    "noise_precision":             {"target": 0.85, "direction": ">="},
    "project_false_noise_rate":    {"target": 0.10, "direction": "<="},
    "hr_recall":                   {"target": 0.90, "direction": ">="},
    "legal_recall":                {"target": 0.90, "direction": ">="},
    "privileged_recall":           {"target": 0.90, "direction": ">="},
}

# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_bool(val: str) -> bool:
    return val.strip().lower() in ("true", "1", "yes")


def _parse_tags(val: str) -> set[str]:
    return {t.strip() for t in val.split(",") if t.strip()}


def _met(value, target, direction) -> bool:
    if value is None:
        return False
    return value >= target if direction == ">=" else value <= target


def _low_conf(n: int) -> str | None:
    if n < LOW_CONF_HARD:
        return "hard"
    if n < LOW_CONF_SOFT:
        return "soft"
    return None


def _safe_div(num: int, den: int) -> float | None:
    return round(num / den, 4) if den > 0 else None


# ── loading ───────────────────────────────────────────────────────────────────

def load_labels(path: Path) -> list[dict]:
    """Load and validate the human label CSV. Raises ValueError on malformed rows."""
    if not path.exists():
        raise FileNotFoundError(f"Label file not found: {path}")

    required = {"sample_id", "actual_noise", "actual_sensitivity", "project_relevant"}
    rows = []
    errors = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Label file {path} is empty or has no header.")
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Label file missing required columns: {sorted(missing)}")

        for i, row in enumerate(reader, start=2):
            an = row.get("actual_noise", "").strip()
            as_ = row.get("actual_sensitivity", "").strip()
            pr = row.get("project_relevant", "").strip()

            if an not in VALID_NOISE:
                errors.append(f"row {i}: actual_noise={an!r} not in {sorted(VALID_NOISE)}")
            if as_ not in VALID_SENSITIVITY:
                errors.append(f"row {i}: actual_sensitivity={as_!r} not in {sorted(VALID_SENSITIVITY)}")
            if pr not in VALID_PROJECT:
                errors.append(f"row {i}: project_relevant={pr!r} not in {sorted(VALID_PROJECT)}")

            rows.append({
                "sample_id":          row.get("sample_id", "").strip(),
                "message_pk":         row.get("message_pk", "").strip(),   # optional; absent in pre-fix files
                "actual_noise":       an,
                "actual_sensitivity": as_,
                "project_relevant":   pr,
                "notes":              row.get("notes", "").strip(),
            })

    if errors:
        raise ValueError(
            f"Malformed label file ({len(errors)} error(s)):\n" +
            "\n".join(f"  {e}" for e in errors[:20]) +
            ("\n  ..." if len(errors) > 20 else "")
        )
    return rows


def load_sample_csvs(report_dir: Path) -> dict[str, dict]:
    """Load noise_samples.csv and sensitivity_samples.csv.
    Returns dict keyed by sample_id with predicted noise + sensitivity."""
    samples: dict[str, dict] = {}

    for fname in ("noise_samples.csv", "sensitivity_samples.csv"):
        path = report_dir / fname
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                sid = row.get("sample_id", "").strip()
                if not sid or sid in samples:
                    continue
                samples[sid] = {
                    "sample_id":           sid,
                    "message_pk":          row.get("message_pk", ""),
                    "predicted_noise":     _parse_bool(row.get("noise", "false")),
                    "predicted_sensitivity": _parse_tags(row.get("sensitivity", "none")),
                }
    return samples


# ── PII scan ──────────────────────────────────────────────────────────────────

# Files intentionally containing raw content — excluded from PII scan.
_SCAN_EXCLUDE = {"local_review.csv", "live_quality_eval.json"}


def _scan_csv_file(path: Path) -> list[str]:
    violations = []
    with path.open(newline="", encoding="utf-8") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            for col, val in row.items():
                if "@" in (val or ""):
                    violations.append(
                        f"{path.name}:{line_no} col='{col}' contains '@' "
                        f"(possible raw email address)"
                    )
    return violations


def _collect_json_violations(obj, fname: str, jpath: str, out: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _collect_json_violations(v, fname, f"{jpath}.{k}", out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _collect_json_violations(v, fname, f"{jpath}[{i}]", out)
    elif isinstance(obj, str) and "@" in obj:
        out.append(f"{fname}: field '{jpath}' contains '@' (possible raw email address)")


def _scan_json_file(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    violations: list[str] = []
    _collect_json_violations(data, path.name, "", violations)
    return violations


def scan_pii(report_dir: Path) -> list[str]:
    """Scan all generated report .csv and .json files for @ signs.

    Skips local_review.csv (intentionally raw) and live_quality_eval.json
    (the eval output itself).  Best-effort — human review before committing
    any output remains required.
    """
    violations: list[str] = []
    if not report_dir.exists():
        return violations
    for path in sorted(report_dir.iterdir()):
        if path.name in _SCAN_EXCLUDE or not path.is_file():
            continue
        if path.suffix == ".csv":
            violations.extend(_scan_csv_file(path))
        elif path.suffix == ".json":
            violations.extend(_scan_json_file(path))
    return violations


# ── metrics ───────────────────────────────────────────────────────────────────

def compute_noise_metrics(joined: list[dict]) -> dict:
    """Precision, recall, FP rate for the noise classifier."""
    usable = [r for r in joined if r["actual_noise"] in ("noise", "not_noise")]
    n = len(usable)

    tp = sum(1 for r in usable if r["predicted_noise"] and r["actual_noise"] == "noise")
    fp = sum(1 for r in usable if r["predicted_noise"] and r["actual_noise"] == "not_noise")
    fn = sum(1 for r in usable if not r["predicted_noise"] and r["actual_noise"] == "noise")
    tn = sum(1 for r in usable if not r["predicted_noise"] and r["actual_noise"] == "not_noise")

    lc = _low_conf(n)
    return {
        "denominator":     n,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision":       _safe_div(tp, tp + fp) if lc != "hard" else None,
        "recall":          _safe_div(tp, tp + fn) if lc != "hard" else None,
        "false_positive_rate": _safe_div(fp, fp + tn) if lc != "hard" else None,
        "low_confidence":  lc,
    }


def compute_sensitivity_metrics(joined: list[dict]) -> dict:
    """Per-class precision and recall for sensitivity tagging."""
    result = {}
    for cls in SENSITIVE_CLASSES:
        usable = [
            r for r in joined
            if r["actual_sensitivity"] in (cls, "none") and
               r["actual_sensitivity"] not in ("unsure", "")
        ]
        n = len(usable)
        tp = sum(1 for r in usable if cls in r["predicted_sensitivity"] and r["actual_sensitivity"] == cls)
        fp = sum(1 for r in usable if cls in r["predicted_sensitivity"] and r["actual_sensitivity"] == "none")
        fn = sum(1 for r in usable if cls not in r["predicted_sensitivity"] and r["actual_sensitivity"] == cls)

        lc = _low_conf(n)
        result[cls] = {
            "denominator":    n,
            "tp": tp, "fp": fp, "fn": fn,
            "precision":      _safe_div(tp, tp + fp) if lc != "hard" else None,
            "recall":         _safe_div(tp, tp + fn) if lc != "hard" else None,
            "low_confidence": lc,
        }
    return result


def compute_project_metrics(joined: list[dict]) -> dict:
    """False-noise rate for project-relevant messages."""
    project = [r for r in joined if r["project_relevant"] == "yes"]
    falsely_noised = [r for r in project if r["predicted_noise"]]
    n = len(project)
    lc = _low_conf(n)
    return {
        "project_relevant_count":         n,
        "project_relevant_falsely_noised": len(falsely_noised),
        "false_noise_rate":               _safe_div(len(falsely_noised), n) if lc != "hard" else None,
        "low_confidence":                 lc,
    }


# ── hard checks ───────────────────────────────────────────────────────────────

def run_hard_checks(
    joined: list[dict],
    pii_violations: list[str],
    label_errors: list[str],
    n_labeled: int,
    pk_mismatches: list[str] | None = None,
    is_legacy_label_file: bool = False,
    allow_legacy_labels: bool = False,
) -> dict[str, dict]:
    checks: dict[str, dict] = {}

    # 1. No PII in reports
    checks["no_pii_in_reports"] = {
        "passed":     len(pii_violations) == 0,
        "violations": pii_violations,
        "note":       "best-effort @-scan of generated CSVs; human review still required",
    }

    # 2. Determinism — enforced by S6.1 design, not re-checked here
    checks["determinism"] = {
        "passed": True,
        "note":   "enforced by live_quality_report.py (sort_keys, LF newlines, seeded sample)",
    }

    # 3. Malformed labels
    checks["malformed_labels"] = {
        "passed":     len(label_errors) == 0,
        "error_count": len(label_errors),
        "errors":     label_errors[:10],
    }

    # 4. Minimum reviewed rows
    checks["min_reviewed_rows"] = {
        "passed":    n_labeled >= MIN_LABELED_FAIL,
        "count":     n_labeled,
        "threshold": MIN_LABELED_FAIL,
        "warning":   n_labeled < MIN_LABELED_WARN,
    }

    # 5. No sensitive false negatives
    sens_fns = [
        r for r in joined
        if r["actual_sensitivity"] in SENSITIVE_CLASSES
        and not any(c in r["predicted_sensitivity"] for c in SENSITIVE_CLASSES)
    ]
    checks["no_sensitive_false_negatives"] = {
        "passed":  len(sens_fns) == 0,
        "fn_count": len(sens_fns),
        "sample_ids": [r["sample_id"] for r in sens_fns],
    }

    # 6. No undocumented project-relevant messages falsely noised
    undoc = [
        r for r in joined
        if r["project_relevant"] == "yes"
        and r["predicted_noise"]
        and not r["notes"]
    ]
    checks["no_undocumented_project_noise"] = {
        "passed":    len(undoc) == 0,
        "count":     len(undoc),
        "sample_ids": [r["sample_id"] for r in undoc],
        "note":      "add notes to label file to document known false positives",
    }

    # 7. Label/report pk match — detects stale label files
    mismatches = pk_mismatches or []
    if is_legacy_label_file and n_labeled > 0:
        if allow_legacy_labels:
            checks["label_report_pk_match"] = {
                "passed":  True,
                "skipped": True,
                "note": "--allow-legacy-labels set; message_pk absent in label file — "
                        "stale-label detection bypassed",
            }
        else:
            checks["label_report_pk_match"] = {
                "passed":  False,
                "skipped": False,
                "mismatch_count": 0,
                "mismatches": [],
                "note": "label file has non-blank labels but no message_pk column. "
                        "Re-generate label_template.csv from the current report and "
                        "transfer labels, or pass --allow-legacy-labels to bypass.",
            }
    else:
        checks["label_report_pk_match"] = {
            "passed":         not mismatches,
            "skipped":        False,
            "mismatch_count": len(mismatches),
            "mismatches":     mismatches[:5],
            "note": "absent message_pk in all label rows means check was skipped "
                    "(no labels yet, or pre-fix file without message_pk column)",
        }

    return checks


# ── soft targets ──────────────────────────────────────────────────────────────

def evaluate_soft_targets(noise: dict, sens: dict, project: dict) -> dict:
    _lc_map = {
        "noise_precision":          noise.get("low_confidence"),
        "project_false_noise_rate": project.get("low_confidence"),
        "hr_recall":                sens.get("hr", {}).get("low_confidence"),
        "legal_recall":             sens.get("legal", {}).get("low_confidence"),
        "privileged_recall":        sens.get("privileged", {}).get("low_confidence"),
    }
    values = {
        "noise_precision":          noise.get("precision"),
        "project_false_noise_rate": project.get("false_noise_rate"),
        "hr_recall":                sens.get("hr", {}).get("recall"),
        "legal_recall":             sens.get("legal", {}).get("recall"),
        "privileged_recall":        sens.get("privileged", {}).get("recall"),
    }
    result = {}
    for key, spec in SOFT_TARGETS.items():
        val = values.get(key)
        lc  = _lc_map.get(key)
        result[key] = {
            "value":          val,
            "target":         spec["target"],
            "direction":      spec["direction"],
            "met":            _met(val, spec["target"], spec["direction"]),
            "low_confidence": lc,
            "note":           "soft target for this sample only — not a universal product threshold",
        }
    return result


# ── entry point ───────────────────────────────────────────────────────────────

def run_eval(
    report_dir: Path,
    labels_path: Path,
    allow_legacy_labels: bool = False,
) -> tuple[dict, bool]:
    """Run the full eval. Returns (result_dict, overall_passed)."""
    if not report_dir.exists():
        raise FileNotFoundError(f"Report directory not found: {report_dir}")

    # PII scan (collect violations, don't raise yet)
    pii_violations = scan_pii(report_dir)

    # Load labels (raises on malformed)
    labels = load_labels(labels_path)
    label_by_id = {r["sample_id"]: r for r in labels}

    # Load sample predictions
    samples = load_sample_csvs(report_dir)

    # Detect stale label files: message_pk in label must match current report
    pk_mismatches: list[str] = []
    for sid, sample in sorted(samples.items()):
        label = label_by_id.get(sid)
        if label is None:
            continue
        label_pk = label.get("message_pk", "")
        if label_pk and label_pk != sample["message_pk"]:
            pk_mismatches.append(
                f"{sid}: label message_pk={label_pk!r} != "
                f"report message_pk={sample['message_pk']!r}"
            )

    # Join predictions with labels
    joined = []
    for sid, sample in sorted(samples.items()):
        label = label_by_id.get(sid, {})
        joined.append({
            **sample,
            "actual_noise":       label.get("actual_noise", ""),
            "actual_sensitivity": label.get("actual_sensitivity", ""),
            "project_relevant":   label.get("project_relevant", ""),
            "notes":              label.get("notes", ""),
        })

    n_labeled = sum(1 for r in joined if r["actual_noise"] in ("noise", "not_noise"))

    # Detect legacy label files: non-blank labels exist but no message_pk column present.
    has_pk_in_labels = any(r.get("message_pk", "") for r in labels)
    is_legacy_file   = n_labeled > 0 and not has_pk_in_labels

    # Metrics
    noise_metrics   = compute_noise_metrics(joined)
    sens_metrics    = compute_sensitivity_metrics(joined)
    project_metrics = compute_project_metrics(joined)
    soft = evaluate_soft_targets(noise_metrics, sens_metrics, project_metrics)

    # Hard checks
    hard = run_hard_checks(
        joined, pii_violations, [], n_labeled, pk_mismatches,
        is_legacy_label_file=is_legacy_file,
        allow_legacy_labels=allow_legacy_labels,
    )

    overall_passed = all(c["passed"] for c in hard.values())

    result = {
        "overall_passed":    overall_passed,
        "coverage": {
            "total_sample_rows": len(joined),
            "labeled_rows":      n_labeled,
            "warn_low_coverage": n_labeled < MIN_LABELED_WARN,
        },
        "hard_checks":          hard,
        "noise_metrics":        noise_metrics,
        "sensitivity_metrics":  sens_metrics,
        "project_metrics":      project_metrics,
        "soft_targets":         soft,
    }
    return result, overall_passed


def _print_summary(result: dict) -> None:
    cov = result["coverage"]
    nm  = result["noise_metrics"]
    pm  = result["project_metrics"]
    hard = result["hard_checks"]

    print("\n── S6.3 Eval Summary ──────────────────────────────────────────")
    print(f"  Reviewed rows : {cov['labeled_rows']} / {cov['total_sample_rows']}")
    if cov.get("warn_low_coverage"):
        print(f"  WARNING: fewer than {MIN_LABELED_WARN} reviewed rows — metrics may be low-confidence")

    print("\n  Hard checks:")
    for name, check in hard.items():
        status = "PASS" if check["passed"] else "FAIL"
        print(f"    [{status}] {name}")
        if not check["passed"] and check.get("errors"):
            for e in check["errors"][:3]:
                print(f"           {e}")
        if not check["passed"] and check.get("sample_ids"):
            print(f"           failing sample_ids: {check['sample_ids'][:5]}")

    print("\n  Noise metrics:")
    _print_metric("    precision",          nm.get("precision"),      nm)
    _print_metric("    recall",             nm.get("recall"),         nm)
    _print_metric("    false_positive_rate", nm.get("false_positive_rate"), nm)

    print("\n  Sensitivity recall (sensitive classes only):")
    for cls in SENSITIVE_CLASSES:
        cm = result["sensitivity_metrics"].get(cls, {})
        r  = cm.get("recall")
        lc = cm.get("low_confidence")
        flag = f"  [low-conf:{lc}]" if lc else ""
        rval = f"{r:.3f}" if r is not None else "n/a"
        print(f"    {cls:<12}: recall={rval}{flag}  (n={cm.get('denominator', 0)})")

    fnr = pm.get("false_noise_rate")
    print(f"\n  Project-relevant false-noise rate: "
          f"{'n/a' if fnr is None else f'{fnr:.3f}'}  "
          f"({pm.get('project_relevant_falsely_noised', 0)} / "
          f"{pm.get('project_relevant_count', 0)})")

    print("\n  Soft targets (this sample only — not product thresholds):")
    for key, st in result["soft_targets"].items():
        lc  = st.get("low_confidence")
        val = f"{st['value']:.3f}" if st["value"] is not None else "n/a"
        if lc == "hard" and st["value"] is None:
            status = "low-confidence / not evaluated"
        elif lc == "soft":
            status = "met (low-confidence)" if st["met"] else "MISS (low-confidence)"
        else:
            status = "met" if st["met"] else "MISS"
        print(f"    {key:<35}: {val}  (target {st['direction']} {st['target']})  [{status}]")

    status = "PASSED" if result["overall_passed"] else "FAILED"
    print(f"\n  Overall: {status}")
    print("───────────────────────────────────────────────────────────────\n")


def _print_metric(label: str, value, metrics: dict) -> None:
    lc = metrics.get("low_confidence")
    flag = f"  [low-conf:{lc}]" if lc else ""
    vstr = f"{value:.3f}" if value is not None else "n/a"
    print(f"  {label:<30}: {vstr}{flag}  (n={metrics.get('denominator', 0)})")


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="S6.3 eval: compute quality metrics from labeled report samples."
    )
    p.add_argument("--report-dir", default=".local/reports/live-quality",
                   help="Directory containing live_quality_report.py outputs.")
    p.add_argument("--labels",
                   default=".local/labels/live-mailbox-labels.csv",
                   help="Human-filled label CSV (S6.2 workflow).")
    p.add_argument("--out", default=None,
                   help="Directory for live_quality_eval.json. Default: same as --report-dir.")
    p.add_argument("--allow-legacy-labels", action="store_true",
                   help="Allow label files without message_pk (pre-fix format). "
                        "WARNING: stale-label detection is bypassed.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    report_dir  = Path(args.report_dir)
    labels_path = Path(args.labels)
    out_dir     = Path(args.out) if args.out else report_dir

    try:
        result, passed = run_eval(
            report_dir, labels_path,
            allow_legacy_labels=args.allow_legacy_labels,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "live_quality_eval.json"
    with out_path.open("w", newline="\n", encoding="utf-8") as f:
        f.write(json.dumps(result, sort_keys=True, indent=2, default=str))
        f.write("\n")

    _print_summary(result)
    print(f"Eval written to: {out_path}")

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
