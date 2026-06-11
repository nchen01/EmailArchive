"""Auto-fill the S6 label template from smoke dataset ground truth.

After running generate_smoke_dataset.py → gmail_smoke_ingest.py →
live_quality_report.py, run this to produce a filled label CSV using the
ground truth written by the generate script.  No human labeling required —
the generated dataset has known ground truth by construction.

The script queries the DB to get message_id_header for each message_pk in
the report, then looks those up in ground_truth.json.

Usage:
  python scripts/fill_smoke_labels.py \\
      --mailbox-id <uuid> \\
      --report-dir .local/reports/smoke-quality \\
      --ground-truth .local/smoke-dataset/ground_truth.json \\
      --out .local/labels/smoke-labels.csv

After running, verify against the eval:
  python scripts/eval_live_quality.py \\
      --report-dir .local/reports/smoke-quality \\
      --labels .local/labels/smoke-labels.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIR = ROOT / ".local"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LABEL_FIELDS = [
    "sample_id", "message_pk", "actual_noise", "actual_sensitivity",
    "project_relevant", "notes", "reviewed_by", "reviewed_at",
]

# Sensitivity gold-label mapping.
# The eval VALID_SENSITIVITY set is: none/hr/legal/privileged/personal/unsure/"".
# Our smoke dataset uses: none/hr/privileged.
# "privileged" messages (legal-msa thread) contain "attorney-client" keywords;
# the LEGAL tag is separate and requires cfg.legal_domains to be configured.
_SENS_MAP = {
    "none":       "none",
    "hr":         "hr",
    "legal":      "legal",
    "privileged": "privileged",
    "personal":   "personal",
}


def load_ground_truth(path: Path) -> dict[str, dict]:
    """Returns mapping from message_id (without angle brackets) → gold label dict."""
    if not path.exists():
        sys.exit(f"ERROR: ground truth file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: ground_truth.json is not valid JSON: {exc}")
    return data.get("messages", {})


def load_report_samples(report_dir: Path) -> dict[str, dict]:
    """Load sample_id → {message_pk, predicted_noise, predicted_sensitivity} from report CSVs."""
    samples: dict[str, dict] = {}
    for fname in ("noise_samples.csv", "sensitivity_samples.csv"):
        path = report_dir / fname
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                sid = row.get("sample_id", "").strip()
                pk  = row.get("message_pk", "").strip()
                if sid and pk and sid not in samples:
                    samples[sid] = {
                        "sample_id":  sid,
                        "message_pk": pk,
                    }
    if not samples:
        sys.exit(
            f"ERROR: no samples found in {report_dir}.\n"
            "Run live_quality_report.py with --include-sensitive first."
        )
    return samples


def fetch_message_id_headers(mailbox_id: str, pks: list[str]) -> dict[str, str]:
    """Query DB: message.id → message.message_id_header for the given PKs."""
    from sqlalchemy import text
    from services.db.engine import SessionLocal

    pk_ids = [pk for pk in pks if pk]
    if not pk_ids:
        return {}

    session = SessionLocal()
    try:
        rows = session.execute(
            text(
                "SELECT id, message_id_header FROM message "
                "WHERE mailbox_id = :mid AND id::text = ANY(:pks)"
            ),
            {"mid": mailbox_id, "pks": pk_ids},
        ).all()
        return {str(r.id): r.message_id_header for r in rows}
    finally:
        session.close()


def fill_labels(
    samples: dict[str, dict],
    pk_to_header: dict[str, str],
    ground_truth: dict[str, dict],
) -> tuple[list[dict], int, int]:
    """Produce filled label rows.

    Returns (rows, matched_count, unmatched_count).
    Unmatched rows (message not in ground truth) get blank labels.
    """
    rows: list[dict] = []
    matched = 0
    unmatched = 0

    for sid, sample in sorted(samples.items()):
        pk  = sample["message_pk"]
        hdr = pk_to_header.get(pk, "")

        # The message_id_header in DB is the raw value from the Message-ID header,
        # which for our generated messages is "smoke-{key}-{idx}@smoke.generated"
        # (without angle brackets — the normalize step strips them).
        gt = ground_truth.get(hdr)

        if gt is None:
            # Try stripping angle brackets in case the DB stores them.
            hdr_stripped = hdr.strip("<>")
            gt = ground_truth.get(hdr_stripped)

        if gt:
            matched += 1
            rows.append({
                "sample_id":          sid,
                "message_pk":         pk,
                "actual_noise":       "noise" if gt["noise"] else "not_noise",
                "actual_sensitivity": _SENS_MAP.get(gt["sensitivity"], "none"),
                "project_relevant":   "yes" if gt["project_relevant"] else "no",
                "notes":              f"auto-filled from smoke ground truth: {gt['thread_key']}-{gt['msg_index']}",
                "reviewed_by":        "generate_smoke_dataset.py",
                "reviewed_at":        "",
            })
        else:
            unmatched += 1
            rows.append({
                "sample_id":          sid,
                "message_pk":         pk,
                "actual_noise":       "",
                "actual_sensitivity": "",
                "project_relevant":   "",
                "notes":              f"WARNING: not in smoke ground truth (header={hdr!r})",
                "reviewed_by":        "",
                "reviewed_at":        "",
            })

    return rows, matched, unmatched


def write_label_csv(rows: list[dict], out_path: Path) -> None:
    if not str(out_path.resolve()).startswith(str(LOCAL_DIR.resolve())):
        sys.exit(
            f"ERROR: output path must be under {LOCAL_DIR} to prevent accidental "
            f"commits of local data. Got: {out_path.resolve()}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="\n", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LABEL_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Auto-fill label template from smoke dataset ground truth."
    )
    p.add_argument("--mailbox-id", required=True, help="UUID of the ingested mailbox.")
    p.add_argument("--report-dir", required=True,
                   help="Directory containing live_quality_report.py output CSVs.")
    p.add_argument("--ground-truth", required=True,
                   help="Path to ground_truth.json written by generate_smoke_dataset.py.")
    p.add_argument("--out", default=str(LOCAL_DIR / "labels" / "smoke-labels.csv"),
                   help="Output label CSV path (must be under .local/).")
    p.add_argument("--allow-unmatched", action="store_true",
                   help="Do not exit nonzero when report rows cannot be matched to ground "
                        "truth. Default is to fail: unmatched rows usually indicate the "
                        "wrong mailbox, an ingestion miss, or a stale ground_truth.json.")
    return p.parse_args(argv)


def main(argv=None):
    from scripts._env import load_local_env
    load_local_env()   # load .env before DATABASE_URL is read by db/engine.py

    args = parse_args(argv)

    report_dir  = Path(args.report_dir)
    gt_path     = Path(args.ground_truth)
    out_path    = Path(args.out)

    if not report_dir.exists():
        sys.exit(f"ERROR: report directory not found: {report_dir}")

    ground_truth = load_ground_truth(gt_path)
    print(f"Ground truth: {len(ground_truth)} messages in {gt_path.name}")

    samples = load_report_samples(report_dir)
    print(f"Report samples: {len(samples)} rows across noise/sensitivity CSVs")

    pks = [s["message_pk"] for s in samples.values()]
    print(f"Querying DB for {len(pks)} message_id_headers...")
    pk_to_header = fetch_message_id_headers(args.mailbox_id, pks)
    print(f"DB returned {len(pk_to_header)} rows")

    rows, matched, unmatched = fill_labels(samples, pk_to_header, ground_truth)

    print(f"\nFilled {matched} / {len(samples)} sample rows from ground truth.")
    if unmatched and not args.allow_unmatched:
        print(
            f"\nERROR: {unmatched} report row(s) could not be matched to ground truth.\n"
            "Common causes:\n"
            "  - Wrong --mailbox-id (different mailbox than the one that received the smoke messages)\n"
            "  - Ingest missed the generated messages (account not empty; increase --max-messages)\n"
            "  - Stale ground_truth.json (regenerate with --dry-run to verify Message-IDs)\n"
            "  - Report generated before ingest completed\n"
            "\nLabel file NOT written. Pass --allow-unmatched to write anyway (unmatched rows\n"
            "will have blank labels and be excluded from eval metrics)."
        )
        sys.exit(1)

    write_label_csv(rows, out_path)
    print(f"\nLabel file written to: {out_path.resolve()}")
    print(f"\nRun eval:")
    print(f"  python scripts/eval_live_quality.py \\")
    print(f"      --report-dir {report_dir} \\")
    print(f"      --labels {out_path}")


if __name__ == "__main__":
    main()
