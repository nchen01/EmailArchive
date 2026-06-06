"""Unit tests for scripts/eval_live_quality.py.

All tests are pure-logic unit tests — no DB, no file system beyond tmp_path.
Covers: parsing, label validation, metric computation, hard checks, soft targets,
PII scan, minimum label counts, and low-confidence marking.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_live_quality import (  # noqa: E402
    LOW_CONF_HARD,
    LOW_CONF_SOFT,
    MIN_LABELED_FAIL,
    MIN_LABELED_WARN,
    _met,
    _parse_bool,
    _parse_tags,
    _safe_div,
    compute_noise_metrics,
    compute_project_metrics,
    compute_sensitivity_metrics,
    evaluate_soft_targets,
    load_labels,
    load_sample_csvs,
    run_eval,
    run_hard_checks,
    scan_pii,
)


# ── _parse_bool ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("val,expected", [
    ("True", True), ("true", True), ("1", True), ("yes", True),
    ("False", False), ("false", False), ("0", False), ("no", False), ("", False),
])
def test_parse_bool(val, expected):
    assert _parse_bool(val) == expected


# ── _parse_tags ───────────────────────────────────────────────────────────────

def test_parse_tags_single():
    assert _parse_tags("none") == {"none"}


def test_parse_tags_multiple():
    assert _parse_tags("hr,none") == {"hr", "none"}


def test_parse_tags_empty():
    assert _parse_tags("") == set()


def test_parse_tags_whitespace():
    assert _parse_tags(" hr , none ") == {"hr", "none"}


# ── _safe_div ─────────────────────────────────────────────────────────────────

def test_safe_div_normal():
    assert _safe_div(3, 4) == 0.75


def test_safe_div_zero_denominator():
    assert _safe_div(5, 0) is None


# ── _met ──────────────────────────────────────────────────────────────────────

def test_met_gte_passes():
    assert _met(0.9, 0.85, ">=") is True


def test_met_gte_fails():
    assert _met(0.8, 0.85, ">=") is False


def test_met_lte_passes():
    assert _met(0.05, 0.10, "<=") is True


def test_met_none_fails():
    assert _met(None, 0.85, ">=") is False


# ── load_labels ───────────────────────────────────────────────────────────────

def _write_label_csv(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "labels.csv"
    fieldnames = ["sample_id", "message_pk", "actual_noise", "actual_sensitivity",
                  "project_relevant", "notes", "reviewed_by", "reviewed_at"]
    with path.open("w", newline="\n", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        for row in rows:
            full = {k: "" for k in fieldnames}
            full.update(row)
            w.writerow(full)
    return path


def test_load_labels_valid(tmp_path):
    path = _write_label_csv(tmp_path, [
        {"sample_id": "noise-0001", "actual_noise": "noise",
         "actual_sensitivity": "none", "project_relevant": "no"},
    ])
    rows = load_labels(path)
    assert len(rows) == 1
    assert rows[0]["actual_noise"] == "noise"


def test_load_labels_blank_values_allowed(tmp_path):
    path = _write_label_csv(tmp_path, [
        {"sample_id": "noise-0001", "actual_noise": "",
         "actual_sensitivity": "", "project_relevant": ""},
    ])
    rows = load_labels(path)
    assert rows[0]["actual_noise"] == ""


def test_load_labels_malformed_noise_raises(tmp_path):
    path = _write_label_csv(tmp_path, [
        {"sample_id": "noise-0001", "actual_noise": "INVALID",
         "actual_sensitivity": "none", "project_relevant": "no"},
    ])
    with pytest.raises(ValueError, match="Malformed label file"):
        load_labels(path)


def test_load_labels_malformed_sensitivity_raises(tmp_path):
    path = _write_label_csv(tmp_path, [
        {"sample_id": "noise-0001", "actual_noise": "noise",
         "actual_sensitivity": "secret", "project_relevant": "no"},
    ])
    with pytest.raises(ValueError, match="Malformed label file"):
        load_labels(path)


def test_load_labels_malformed_project_raises(tmp_path):
    path = _write_label_csv(tmp_path, [
        {"sample_id": "noise-0001", "actual_noise": "noise",
         "actual_sensitivity": "none", "project_relevant": "maybe"},
    ])
    with pytest.raises(ValueError, match="Malformed label file"):
        load_labels(path)


def test_load_labels_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_labels(tmp_path / "nonexistent.csv")


def test_load_labels_missing_column_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("sample_id,actual_noise\nnoise-0001,noise\n")
    with pytest.raises(ValueError, match="missing required columns"):
        load_labels(path)


def test_load_labels_empty_file_raises(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("")
    with pytest.raises(ValueError):
        load_labels(path)


def test_load_labels_duplicate_sample_id_raises(tmp_path):
    path = _write_label_csv(tmp_path, [
        {"sample_id": "noise-0001", "actual_noise": "noise",
         "actual_sensitivity": "none", "project_relevant": "no"},
        {"sample_id": "noise-0001", "actual_noise": "not_noise",  # duplicate
         "actual_sensitivity": "none", "project_relevant": "no"},
    ])
    with pytest.raises(ValueError, match="duplicate sample_id"):
        load_labels(path)


# ── compute_noise_metrics ─────────────────────────────────────────────────────

def _make_joined(specs: list[tuple]) -> list[dict]:
    """specs = list of (predicted_noise, actual_noise, actual_sens, proj_relevant, notes)"""
    rows = []
    for i, (pn, an, as_, pr, notes) in enumerate(specs):
        rows.append({
            "sample_id":             f"noise-{i:04d}",
            "message_pk":            f"pk-{i}",
            "predicted_noise":       pn,
            "predicted_sensitivity": {"none"},
            "actual_noise":          an,
            "actual_sensitivity":    as_,
            "project_relevant":      pr,
            "notes":                 notes,
        })
    return rows


def test_noise_metrics_perfect_precision():
    joined = _make_joined([(True, "noise", "none", "no", "")] * 10 +
                          [(False, "not_noise", "none", "no", "")] * 10)
    m = compute_noise_metrics(joined)
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0
    assert m["low_confidence"] is None


def test_noise_metrics_known_values():
    # 8 TP, 2 FP, 3 FN, 7 TN  → precision=0.8, recall=0.727
    joined = _make_joined(
        [(True, "noise", "none", "no", "")] * 8 +
        [(True, "not_noise", "none", "no", "")] * 2 +
        [(False, "noise", "none", "no", "")] * 3 +
        [(False, "not_noise", "none", "no", "")] * 7
    )
    m = compute_noise_metrics(joined)
    assert m["tp"] == 8
    assert m["fp"] == 2
    assert m["fn"] == 3
    assert abs(m["precision"] - 0.8) < 0.001
    assert abs(m["recall"] - 0.7273) < 0.001


def test_noise_metrics_skips_unsure():
    joined = _make_joined([(True, "unsure", "none", "no", "")] * 5 +
                          [(True, "noise", "none", "no", "")] * 5)
    m = compute_noise_metrics(joined)
    assert m["denominator"] == 5


def test_noise_metrics_low_confidence_hard(tmp_path):
    joined = _make_joined([(True, "noise", "none", "no", "")] * (LOW_CONF_HARD - 1))
    m = compute_noise_metrics(joined)
    assert m["low_confidence"] == "hard"
    assert m["precision"] is None


def test_noise_metrics_low_confidence_soft():
    n = (LOW_CONF_HARD + LOW_CONF_SOFT) // 2
    joined = _make_joined([(True, "noise", "none", "no", "")] * n)
    m = compute_noise_metrics(joined)
    assert m["low_confidence"] == "soft"
    assert m["precision"] is not None


# ── compute_sensitivity_metrics ───────────────────────────────────────────────

def _make_sens_joined(specs):
    """specs = list of (predicted_tags_set, actual_sensitivity)"""
    rows = []
    for i, (pt, as_) in enumerate(specs):
        rows.append({
            "sample_id":             f"sens-{i:04d}",
            "message_pk":            f"pk-s-{i}",
            "predicted_noise":       False,
            "predicted_sensitivity": pt,
            "actual_noise":          "not_noise",
            "actual_sensitivity":    as_,
            "project_relevant":      "no",
            "notes":                 "",
        })
    return rows


def test_sensitivity_recall_perfect():
    joined = _make_sens_joined([({"hr"}, "hr")] * 15 + [({"none"}, "none")] * 15)
    m = compute_sensitivity_metrics(joined)
    assert m["hr"]["recall"] == 1.0
    assert m["hr"]["tp"] == 15


def test_sensitivity_recall_miss():
    # 10 actual hr, 8 predicted, 2 missed
    joined = _make_sens_joined([({"hr"}, "hr")] * 8 + [({"none"}, "hr")] * 2 +
                                [({"none"}, "none")] * 10)
    m = compute_sensitivity_metrics(joined)
    assert m["hr"]["fn"] == 2
    assert abs(m["hr"]["recall"] - 0.8) < 0.001


def test_sensitivity_metrics_low_confidence_when_no_class_data():
    joined = _make_sens_joined([({"none"}, "none")] * 3)
    m = compute_sensitivity_metrics(joined)
    # hr denominator = 3 (only 'none' labeled) — but all are 'none' so TP/FN = 0
    assert m["hr"]["low_confidence"] == "hard"


# ── compute_project_metrics ───────────────────────────────────────────────────

def test_project_metrics_no_false_noise():
    # 20 rows >= LOW_CONF_SOFT (20) → low_confidence is None
    joined = _make_joined([(False, "not_noise", "none", "yes", "")] * 20)
    m = compute_project_metrics(joined)
    assert m["false_noise_rate"] == 0.0
    assert m["project_relevant_falsely_noised"] == 0
    assert m["low_confidence"] is None


def test_project_metrics_some_false_noise():
    # 4 + 16 = 20 total project rows → low_confidence is None
    joined = (
        _make_joined([(True, "noise", "none", "yes", "")] * 4) +
        _make_joined([(False, "not_noise", "none", "yes", "")] * 16)
    )
    m = compute_project_metrics(joined)
    assert m["project_relevant_falsely_noised"] == 4
    assert abs(m["false_noise_rate"] - 0.2) < 0.001
    assert m["low_confidence"] is None


def test_project_metrics_no_project_rows():
    joined = _make_joined([(True, "noise", "none", "no", "")] * 5)
    m = compute_project_metrics(joined)
    assert m["project_relevant_count"] == 0
    assert m["false_noise_rate"] is None
    assert m["low_confidence"] == "hard"  # 0 rows


def test_project_metrics_low_confidence_hard():
    joined = _make_joined([(False, "not_noise", "none", "yes", "")] * (LOW_CONF_HARD - 1))
    m = compute_project_metrics(joined)
    assert m["low_confidence"] == "hard"
    assert m["false_noise_rate"] is None


def test_project_metrics_low_confidence_soft():
    n = (LOW_CONF_HARD + LOW_CONF_SOFT) // 2
    joined = _make_joined([(False, "not_noise", "none", "yes", "")] * n)
    m = compute_project_metrics(joined)
    assert m["low_confidence"] == "soft"
    assert m["false_noise_rate"] is not None


# ── run_hard_checks ───────────────────────────────────────────────────────────

def test_hard_check_pii_violation_fails():
    joined = []
    checks = run_hard_checks(joined, ["noise_samples.csv:2 col='sender_domain' contains '@'"], [], 50)
    assert not checks["no_pii_in_reports"]["passed"]


def test_hard_check_pii_clean_passes():
    checks = run_hard_checks([], [], [], 50)
    assert checks["no_pii_in_reports"]["passed"]


def test_hard_check_min_rows_fails_under_threshold():
    checks = run_hard_checks([], [], [], MIN_LABELED_FAIL - 1)
    assert not checks["min_reviewed_rows"]["passed"]


def test_hard_check_min_rows_warns_under_100():
    checks = run_hard_checks([], [], [], MIN_LABELED_FAIL + 1)
    assert checks["min_reviewed_rows"]["passed"]
    assert checks["min_reviewed_rows"]["warning"] is True


def test_hard_check_min_rows_no_warning_above_100():
    checks = run_hard_checks([], [], [], MIN_LABELED_WARN + 1)
    assert checks["min_reviewed_rows"]["passed"]
    assert checks["min_reviewed_rows"]["warning"] is False


def test_hard_check_sensitive_fn_fails():
    joined = [{
        "sample_id": "sens-0001",
        "message_pk": "pk-1",
        "predicted_noise": False,
        "predicted_sensitivity": {"none"},
        "actual_noise": "not_noise",
        "actual_sensitivity": "hr",
        "project_relevant": "no",
        "notes": "",
    }]
    checks = run_hard_checks(joined, [], [], 50)
    assert not checks["no_sensitive_false_negatives"]["passed"]
    assert checks["no_sensitive_false_negatives"]["fn_count"] == 1


def test_hard_check_sensitive_fn_passes_when_predicted():
    joined = [{
        "sample_id": "sens-0001",
        "message_pk": "pk-1",
        "predicted_noise": False,
        "predicted_sensitivity": {"hr"},
        "actual_noise": "not_noise",
        "actual_sensitivity": "hr",
        "project_relevant": "no",
        "notes": "",
    }]
    checks = run_hard_checks(joined, [], [], 50)
    assert checks["no_sensitive_false_negatives"]["passed"]


def test_hard_check_undocumented_project_noise_fails():
    joined = [{
        "sample_id": "noise-0001",
        "message_pk": "pk-1",
        "predicted_noise": True,
        "predicted_sensitivity": {"none"},
        "actual_noise": "not_noise",
        "actual_sensitivity": "none",
        "project_relevant": "yes",
        "notes": "",  # no notes = undocumented
    }]
    checks = run_hard_checks(joined, [], [], 50)
    assert not checks["no_undocumented_project_noise"]["passed"]


def test_hard_check_documented_project_noise_passes():
    joined = [{
        "sample_id": "noise-0001",
        "message_pk": "pk-1",
        "predicted_noise": True,
        "predicted_sensitivity": {"none"},
        "actual_noise": "not_noise",
        "actual_sensitivity": "none",
        "project_relevant": "yes",
        "notes": "Known: this is a newsletter subscription confirmation.",
    }]
    checks = run_hard_checks(joined, [], [], 50)
    assert checks["no_undocumented_project_noise"]["passed"]


def test_hard_check_stale_label_fails():
    checks = run_hard_checks(
        [], [], [], 50,
        pk_mismatches=["noise-0001: label pk='old-pk' != report pk='new-pk'"],
    )
    assert not checks["label_report_pk_match"]["passed"]
    assert checks["label_report_pk_match"]["mismatch_count"] == 1


def test_hard_check_pk_match_passes_when_no_mismatches():
    checks = run_hard_checks([], [], [], 50, pk_mismatches=[])
    assert checks["label_report_pk_match"]["passed"]


def test_hard_check_pk_match_skipped_for_pre_fix_labels():
    # pk_mismatches=None means no message_pk in label file — check skipped (passes)
    checks = run_hard_checks([], [], [], 50, pk_mismatches=None)
    assert checks["label_report_pk_match"]["passed"]


def test_hard_check_legacy_file_with_labels_fails_without_flag():
    """Non-blank labels + no message_pk column → hard check fails by default."""
    checks = run_hard_checks(
        [], [], [], 50, pk_mismatches=None,
        is_legacy_label_file=True, allow_legacy_labels=False,
    )
    assert not checks["label_report_pk_match"]["passed"]
    assert not checks["label_report_pk_match"]["skipped"]


def test_hard_check_legacy_file_passes_with_allow_flag():
    """--allow-legacy-labels bypasses the check and sets skipped=True."""
    checks = run_hard_checks(
        [], [], [], 50, pk_mismatches=None,
        is_legacy_label_file=True, allow_legacy_labels=True,
    )
    assert checks["label_report_pk_match"]["passed"]
    assert checks["label_report_pk_match"]["skipped"]


def test_hard_check_legacy_file_no_labels_passes():
    """Legacy file with no non-blank labels → no risk, check passes normally."""
    checks = run_hard_checks(
        [], [], [], 0, pk_mismatches=None,  # n_labeled=0
        is_legacy_label_file=False, allow_legacy_labels=False,
    )
    assert checks["label_report_pk_match"]["passed"]


# ── unknown label sample_ids ──────────────────────────────────────────────────

def test_hard_check_unknown_label_ids_fails():
    checks = run_hard_checks(
        [], [], [], 50,
        unknown_label_ids=["noise-9999", "noise-8888"],
    )
    assert not checks["no_unknown_label_ids"]["passed"]
    assert checks["no_unknown_label_ids"]["count"] == 2


def test_hard_check_unknown_label_ids_passes_when_empty():
    checks = run_hard_checks([], [], [], 50, unknown_label_ids=[])
    assert checks["no_unknown_label_ids"]["passed"]


def test_hard_check_unknown_label_ids_passes_when_none():
    checks = run_hard_checks([], [], [], 50, unknown_label_ids=None)
    assert checks["no_unknown_label_ids"]["passed"]


# ── scan_pii ──────────────────────────────────────────────────────────────────

def test_scan_pii_clean_report_passes(tmp_path):
    path = tmp_path / "noise_samples.csv"
    with path.open("w", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["sample_id", "sender_domain", "sensitivity"],
                           lineterminator="\n")
        w.writeheader()
        w.writerow({"sample_id": "noise-0001", "sender_domain": "example.com",
                    "sensitivity": "none"})
    assert scan_pii(tmp_path) == []


def test_scan_pii_detects_at_sign_in_csv(tmp_path):
    path = tmp_path / "noise_samples.csv"
    with path.open("w", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["sample_id", "sender_domain"],
                           lineterminator="\n")
        w.writeheader()
        w.writerow({"sample_id": "noise-0001", "sender_domain": "user@example.com"})
    violations = scan_pii(tmp_path)
    assert len(violations) == 1
    assert "sender_domain" in violations[0]


def test_scan_pii_detects_at_sign_in_json(tmp_path):
    (tmp_path / "summary.json").write_text(
        '{"owner_email": "user@example.com", "count": 10}', encoding="utf-8"
    )
    violations = scan_pii(tmp_path)
    assert any("summary.json" in v for v in violations)
    assert any("owner_email" in v for v in violations)


def test_scan_pii_excludes_local_review_csv(tmp_path):
    path = tmp_path / "local_review.csv"
    with path.open("w", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["subject"], lineterminator="\n")
        w.writeheader()
        w.writerow({"subject": "Meeting with user@example.com"})
    # local_review.csv is intentionally excluded
    assert scan_pii(tmp_path) == []


def test_scan_pii_excludes_eval_json(tmp_path):
    (tmp_path / "live_quality_eval.json").write_text(
        '{"note": "user@example.com"}', encoding="utf-8"
    )
    assert scan_pii(tmp_path) == []


def test_scan_pii_missing_files_skip_gracefully(tmp_path):
    assert scan_pii(tmp_path) == []


# ── load_sample_csvs duplicate detection ──────────────────────────────────────

def test_load_sample_csvs_duplicate_raises(tmp_path):
    path = tmp_path / "noise_samples.csv"
    with path.open("w", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["sample_id", "message_pk", "noise", "sensitivity"],
                           lineterminator="\n")
        w.writeheader()
        w.writerow({"sample_id": "noise-0001", "message_pk": "pk-1", "noise": "True", "sensitivity": "none"})
        w.writerow({"sample_id": "noise-0001", "message_pk": "pk-2", "noise": "False", "sensitivity": "none"})
    with pytest.raises(ValueError, match="Duplicate sample_id"):
        load_sample_csvs(tmp_path)


def test_load_sample_csvs_no_duplicate_passes(tmp_path):
    path = tmp_path / "noise_samples.csv"
    with path.open("w", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["sample_id", "message_pk", "noise", "sensitivity"],
                           lineterminator="\n")
        w.writeheader()
        w.writerow({"sample_id": "noise-0001", "message_pk": "pk-1", "noise": "True", "sensitivity": "none"})
        w.writerow({"sample_id": "noise-0002", "message_pk": "pk-2", "noise": "False", "sensitivity": "none"})
    samples = load_sample_csvs(tmp_path)
    assert len(samples) == 2


# ── evaluate_soft_targets ─────────────────────────────────────────────────────

def test_soft_targets_all_met():
    noise = {"precision": 0.90, "denominator": 50, "low_confidence": None}
    sens  = {cls: {"recall": 0.95, "denominator": 20, "low_confidence": None}
             for cls in ["hr", "legal", "privileged", "personal"]}
    proj  = {"false_noise_rate": 0.05, "project_relevant_count": 20}
    soft = evaluate_soft_targets(noise, sens, proj)
    assert soft["noise_precision"]["met"] is True
    assert soft["project_false_noise_rate"]["met"] is True
    assert soft["hr_recall"]["met"] is True


def test_soft_targets_miss_does_not_raise():
    noise = {"precision": 0.70, "denominator": 50, "low_confidence": None}
    sens  = {cls: {"recall": 0.80, "denominator": 20, "low_confidence": None}
             for cls in ["hr", "legal", "privileged", "personal"]}
    proj  = {"false_noise_rate": 0.20, "project_relevant_count": 10}
    soft = evaluate_soft_targets(noise, sens, proj)
    assert soft["noise_precision"]["met"] is False
    assert soft["hr_recall"]["met"] is False


# ── run_eval integration ──────────────────────────────────────────────────────

def _write_sample_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="\n", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _make_minimal_report_dir(tmp_path: Path, n_noise: int = 40, n_not_noise: int = 40) -> Path:
    """Write minimal report + label CSVs sufficient for a passing eval."""
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()

    noise_rows = []
    label_rows = []
    for i in range(n_noise):
        sid = f"noise-{i+1:04d}"
        pk = f"pk-{i}"
        noise_rows.append({
            "sample_id": sid, "message_pk": pk,
            "message_header_hash": "abcdef0123456789",
            "sender_domain": "example.com", "noise": "True",
            "sensitivity": "none", "has_owner_reply_in_thread": "False",
            "thread_message_count": "3", "subject_chars": "20",
            "clean_text_chars": "200", "date_day": "2026-01-01",
        })
        label_rows.append({
            "sample_id": sid, "message_pk": pk,  # must match report
            "actual_noise": "noise",
            "actual_sensitivity": "none", "project_relevant": "no",
            "notes": "", "reviewed_by": "", "reviewed_at": "",
        })
    for i in range(n_not_noise):
        sid = f"noise-{n_noise+i+1:04d}"
        pk = f"pk-nn-{i}"
        noise_rows.append({
            "sample_id": sid, "message_pk": pk,
            "message_header_hash": "abcdef0123456789",
            "sender_domain": "example.com", "noise": "False",
            "sensitivity": "none", "has_owner_reply_in_thread": "False",
            "thread_message_count": "1", "subject_chars": "10",
            "clean_text_chars": "100", "date_day": "2026-01-01",
        })
        label_rows.append({
            "sample_id": sid, "message_pk": pk,  # must match report
            "actual_noise": "not_noise",
            "actual_sensitivity": "none", "project_relevant": "no",
            "notes": "", "reviewed_by": "", "reviewed_at": "",
        })

    _write_sample_csv(
        report_dir / "noise_samples.csv", noise_rows,
        ["sample_id", "message_pk", "message_header_hash", "sender_domain",
         "noise", "sensitivity", "has_owner_reply_in_thread",
         "thread_message_count", "subject_chars", "clean_text_chars", "date_day"],
    )
    _write_sample_csv(
        report_dir / "sensitivity_samples.csv", [],
        ["sample_id", "message_pk", "message_header_hash", "sender_domain",
         "sensitivity", "noise", "subject_chars", "clean_text_chars", "date_day"],
    )
    _write_label_csv(labels_dir, label_rows)
    return report_dir, labels_dir / "labels.csv"


def test_run_eval_passes_on_clean_data(tmp_path):
    report_dir, labels_path = _make_minimal_report_dir(tmp_path, 40, 40)
    result, passed = run_eval(report_dir, labels_path)
    assert passed
    assert result["overall_passed"]
    assert result["coverage"]["labeled_rows"] == 80


def test_run_eval_fails_when_too_few_labels(tmp_path):
    report_dir, labels_path = _make_minimal_report_dir(tmp_path, 10, 10)
    result, passed = run_eval(report_dir, labels_path)
    assert not passed
    assert not result["hard_checks"]["min_reviewed_rows"]["passed"]


def test_run_eval_fails_on_sensitive_fn(tmp_path):
    report_dir, labels_path = _make_minimal_report_dir(tmp_path, 40, 40)
    # Inject an HR-labeled row that was predicted as non-sensitive
    with (report_dir / "sensitivity_samples.csv").open("a", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["sample_id", "message_pk",
            "message_header_hash", "sender_domain", "sensitivity",
            "noise", "subject_chars", "clean_text_chars", "date_day"],
            lineterminator="\n")
        w.writerow({
            "sample_id": "sens-0001", "message_pk": "pk-sens-1",
            "message_header_hash": "abcdef0123456789",
            "sender_domain": "hr-corp.com", "sensitivity": "none",
            "noise": "False", "subject_chars": "15",
            "clean_text_chars": "80", "date_day": "2026-01-01",
        })
    # Add label: human says this was hr, system missed it
    with labels_path.open("a", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["sample_id", "message_pk",
            "actual_noise", "actual_sensitivity", "project_relevant",
            "notes", "reviewed_by", "reviewed_at"], lineterminator="\n")
        w.writerow({
            "sample_id": "sens-0001", "message_pk": "pk-sens-1",
            "actual_noise": "not_noise",
            "actual_sensitivity": "hr", "project_relevant": "no",
            "notes": "", "reviewed_by": "", "reviewed_at": "",
        })
    result, passed = run_eval(report_dir, labels_path)
    assert not passed
    assert not result["hard_checks"]["no_sensitive_false_negatives"]["passed"]


def test_run_eval_fails_on_stale_label_pk(tmp_path):
    """Eval fails when label message_pk does not match current report."""
    report_dir, labels_path = _make_minimal_report_dir(tmp_path, 40, 40)
    # Corrupt one label row's message_pk so it looks like a stale file
    import csv as _csv
    rows = labels_path.read_text().splitlines()
    header = rows[0]
    first_data = rows[1]
    # Replace the message_pk value in the first data row with a wrong pk
    cols = first_data.split(",")
    pk_idx = header.split(",").index("message_pk")
    cols[pk_idx] = "stale-wrong-pk"
    rows[1] = ",".join(cols)
    labels_path.write_text("\n".join(rows) + "\n")

    result, passed = run_eval(report_dir, labels_path)
    assert not passed
    assert not result["hard_checks"]["label_report_pk_match"]["passed"]
    assert result["hard_checks"]["label_report_pk_match"]["mismatch_count"] >= 1


def test_run_eval_unknown_label_ids_fails(tmp_path):
    """Label rows referencing sample_ids not in the current report fail the eval."""
    report_dir, labels_path = _make_minimal_report_dir(tmp_path, 40, 40)
    # Append a label row with a sample_id that doesn't exist in the report
    with labels_path.open("a", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["sample_id", "message_pk",
            "actual_noise", "actual_sensitivity", "project_relevant",
            "notes", "reviewed_by", "reviewed_at"], lineterminator="\n")
        w.writerow({
            "sample_id": "noise-9999", "message_pk": "pk-stale",
            "actual_noise": "noise",  # non-blank → triggers check
            "actual_sensitivity": "none", "project_relevant": "no",
            "notes": "", "reviewed_by": "", "reviewed_at": "",
        })
    result, passed = run_eval(report_dir, labels_path)
    assert not passed
    assert not result["hard_checks"]["no_unknown_label_ids"]["passed"]
    assert "noise-9999" in result["hard_checks"]["no_unknown_label_ids"]["sample_ids"]


def test_run_eval_unlabeled_unknown_ids_pass(tmp_path):
    """Label rows with blank actual_noise don't trigger the unknown-ID check."""
    report_dir, labels_path = _make_minimal_report_dir(tmp_path, 40, 40)
    with labels_path.open("a", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["sample_id", "message_pk",
            "actual_noise", "actual_sensitivity", "project_relevant",
            "notes", "reviewed_by", "reviewed_at"], lineterminator="\n")
        w.writerow({
            "sample_id": "noise-9999", "message_pk": "",
            "actual_noise": "",  # blank → not counted as labeled, no trigger
            "actual_sensitivity": "", "project_relevant": "",
            "notes": "", "reviewed_by": "", "reviewed_at": "",
        })
    result, passed = run_eval(report_dir, labels_path)
    assert result["hard_checks"]["no_unknown_label_ids"]["passed"]


def test_run_eval_legacy_label_file_fails_without_flag(tmp_path):
    """run_eval rejects a label file with non-blank labels but no message_pk column."""
    report_dir, _ = _make_minimal_report_dir(tmp_path, 40, 40)
    # Write a label file in the old format (no message_pk column)
    legacy_labels = tmp_path / "legacy_labels.csv"
    legacy_labels.write_text(
        "sample_id,actual_noise,actual_sensitivity,project_relevant,notes\n" +
        "".join(f"noise-{i+1:04d},noise,none,no,\n" for i in range(40)) +
        "".join(f"noise-{i+41:04d},not_noise,none,no,\n" for i in range(40)),
        encoding="utf-8",
    )
    result, passed = run_eval(report_dir, legacy_labels, allow_legacy_labels=False)
    assert not passed
    assert not result["hard_checks"]["label_report_pk_match"]["passed"]


def test_run_eval_legacy_label_file_passes_with_flag(tmp_path):
    """--allow-legacy-labels bypasses stale-label check for old label files."""
    report_dir, _ = _make_minimal_report_dir(tmp_path, 40, 40)
    legacy_labels = tmp_path / "legacy_labels.csv"
    legacy_labels.write_text(
        "sample_id,actual_noise,actual_sensitivity,project_relevant,notes\n" +
        "".join(f"noise-{i+1:04d},noise,none,no,\n" for i in range(40)) +
        "".join(f"noise-{i+41:04d},not_noise,none,no,\n" for i in range(40)),
        encoding="utf-8",
    )
    result, passed = run_eval(report_dir, legacy_labels, allow_legacy_labels=True)
    assert result["hard_checks"]["label_report_pk_match"]["passed"]
    assert result["hard_checks"]["label_report_pk_match"]["skipped"]


def test_run_eval_missing_report_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_eval(tmp_path / "nonexistent", tmp_path / "labels.csv")


def test_run_eval_malformed_label_raises(tmp_path):
    report_dir, _ = _make_minimal_report_dir(tmp_path)
    bad_labels = tmp_path / "bad_labels.csv"
    bad_labels.write_text(
        "sample_id,actual_noise,actual_sensitivity,project_relevant,notes\n"
        "noise-0001,INVALID,none,no,\n"
    )
    with pytest.raises(ValueError, match="Malformed label file"):
        run_eval(report_dir, bad_labels)
