"""S6 live quality report — reads Postgres, writes .local/ reports.

Generates privacy-safe CSV/JSON sampling reports from an already-ingested
mailbox. Never calls Gmail or any external API.

Usage:
    python scripts/live_quality_report.py --mailbox-id <uuid>
    python scripts/live_quality_report.py --mailbox-id <uuid> \\
        --out .local/reports/live-quality --sample-size 50 --seed 42

All outputs are written under --out (default .local/reports/live-quality).
Reports must never be committed for real or throwaway mailboxes; use synthetic
fixtures for any committed examples.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIR = ROOT / ".local"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402

from services.db import models as orm  # noqa: E402
from services.db.engine import SessionLocal  # noqa: E402

DEFAULT_OUT = ".local/reports/live-quality"
DEFAULT_SAMPLE_SIZE = 50
DEFAULT_SEED = 42

# ── privacy helpers ──────────────────────────────────────────────────────────

def _hash_id(value: str, mailbox_id: str) -> str:
    """SHA-256 of mailbox_id:value, truncated to 16 hex chars."""
    return hashlib.sha256(f"{mailbox_id}:{value}".encode()).hexdigest()[:16]


def _sender_domain(email: str) -> str:
    parts = email.rsplit("@", 1)
    return parts[1] if len(parts) == 2 else email


def _date_day(ts) -> str:
    if ts is None:
        return ""
    return str(ts)[:10]


# ── deterministic sampling ───────────────────────────────────────────────────

def _stable_sample(rows: list, n: int, seed: int, label: str = "") -> list:
    """Return up to n items using a seeded RNG. rows must be pre-sorted by caller."""
    if len(rows) < n:
        tag = f"[{label}] " if label else ""
        warnings.warn(
            f"{tag}only {len(rows)} rows available, requested {n}; "
            f"emitting all {len(rows)} (denominator reflects actual count)",
            stacklevel=3,
        )
    if len(rows) <= n:
        return list(rows)
    return random.Random(seed).sample(rows, n)


# ── output helpers ───────────────────────────────────────────────────────────

def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="\n", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="\n", encoding="utf-8") as f:
        f.write(json.dumps(data, sort_keys=True, indent=2, default=str))
        f.write("\n")


def _assert_under_local(path: Path) -> None:
    local = LOCAL_DIR.resolve()
    try:
        path.resolve().relative_to(local)
    except ValueError:
        sys.exit(
            f"ERROR: output path '{path.resolve()}' is not under '{local}'.\n"
            "--emit-local-review-file may only write under .local/ "
            "(hard safety constraint)."
        )


# ── summary ──────────────────────────────────────────────────────────────────

def build_summary(session, mailbox_id: str, owner_email: str) -> dict:
    mid = mailbox_id

    def _scalar(sql, params=None):
        return session.execute(text(sql), params or {"mid": mid}).scalar() or 0

    msg_total    = _scalar("SELECT COUNT(*) FROM message WHERE mailbox_id = :mid")
    noise_count  = _scalar("SELECT COUNT(*) FROM message WHERE mailbox_id = :mid AND noise")
    thread_count = _scalar("SELECT COUNT(*) FROM thread  WHERE mailbox_id = :mid")
    person_count = _scalar("SELECT COUNT(*) FROM person  WHERE mailbox_id = :mid")
    edge_count   = _scalar("SELECT COUNT(*) FROM edge    WHERE mailbox_id = :mid")
    synthetic_count = _scalar(
        "SELECT COUNT(*) FROM message WHERE mailbox_id = :mid "
        "AND (message_id_header = '' OR message_id_header LIKE 'synthetic-%')"
    )

    sens_rows = session.execute(
        text(
            "SELECT unnest(sensitivity) AS tag, COUNT(*) AS cnt "
            "FROM message WHERE mailbox_id = :mid GROUP BY tag ORDER BY cnt DESC"
        ),
        {"mid": mid},
    ).all()
    sensitivity_dist = {r.tag: r.cnt for r in sens_rows}

    domain_rows = session.execute(
        text(
            "SELECT split_part(sender_email,'@',2) AS domain, COUNT(*) AS cnt "
            "FROM message WHERE mailbox_id = :mid "
            "GROUP BY domain ORDER BY cnt DESC LIMIT 25"
        ),
        {"mid": mid},
    ).all()
    top_by_count = [{"domain": r.domain, "count": r.cnt} for r in domain_rows]

    noise_rate_rows = session.execute(
        text(
            "SELECT split_part(sender_email,'@',2) AS domain, "
            "COUNT(*) AS cnt, "
            "COUNT(*) FILTER (WHERE noise)::float / COUNT(*) AS noise_rate "
            "FROM message WHERE mailbox_id = :mid "
            "GROUP BY domain HAVING COUNT(*) >= 5 "
            "ORDER BY noise_rate DESC LIMIT 25"
        ),
        {"mid": mid},
    ).all()
    top_by_noise_rate = [
        {"domain": r.domain, "count": r.cnt, "noise_rate": round(r.noise_rate, 4)}
        for r in noise_rate_rows
    ]

    size_rows = session.execute(
        text(
            "SELECT bucket, COUNT(*) AS thread_count FROM ("
            "  SELECT thread_id, CASE "
            "    WHEN COUNT(*) = 1       THEN '1' "
            "    WHEN COUNT(*) <= 5      THEN '2-5' "
            "    WHEN COUNT(*) <= 20     THEN '6-20' "
            "    ELSE '21+' END AS bucket "
            "  FROM message WHERE mailbox_id = :mid GROUP BY thread_id"
            ") t GROUP BY bucket ORDER BY bucket"
        ),
        {"mid": mid},
    ).all()
    thread_size_dist = {r.bucket: r.thread_count for r in size_rows}

    owner_thread_count = _scalar(
        "SELECT COUNT(DISTINCT thread_id) FROM message "
        "WHERE mailbox_id = :mid AND sender_email = :owner",
        {"mid": mid, "owner": owner_email},
    )

    graph_density = 0.0
    if person_count > 1:
        max_edges = person_count * (person_count - 1) // 2
        graph_density = round(edge_count / max_edges, 6)

    return {
        "mailbox_id": mid,
        "message_count": msg_total,
        "thread_count": thread_count,
        "person_count": person_count,
        "edge_count": edge_count,
        "noise_count": noise_count,
        "noise_pct": round(noise_count / msg_total, 4) if msg_total else 0.0,
        "synthetic_message_id_count": synthetic_count,
        "sensitivity_distribution": sensitivity_dist,
        "top_sender_domains_by_count": top_by_count,
        "top_sender_domains_by_noise_rate": top_by_noise_rate,
        "thread_size_distribution": thread_size_dist,
        "owner_sent_thread_count": owner_thread_count,
        "graph_density": graph_density,
    }


# ── noise samples ─────────────────────────────────────────────────────────────

def sample_noise(
    session, mailbox_id: str, owner_email: str, n: int, seed: int, hash_fn
) -> tuple[list[dict], int, int]:
    """Return (rows, noise_denominator, not_noise_denominator).

    Stratified: up to n rows from noise=True and up to n rows from noise=False,
    sampled independently so both buckets are represented.
    """
    mid = mailbox_id

    thread_counts = dict(
        session.execute(
            text(
                "SELECT thread_id, COUNT(*) FROM message "
                "WHERE mailbox_id = :mid GROUP BY thread_id"
            ),
            {"mid": mid},
        ).all()
    )
    owner_threads = {
        r[0]
        for r in session.execute(
            text(
                "SELECT DISTINCT thread_id FROM message "
                "WHERE mailbox_id = :mid AND sender_email = :owner"
            ),
            {"mid": mid, "owner": owner_email},
        ).all()
    }

    all_rows = session.execute(
        text(
            "SELECT id, message_id_header, sender_email, noise, sensitivity, "
            "subject, clean_text, ts, thread_id "
            "FROM message WHERE mailbox_id = :mid ORDER BY id"
        ),
        {"mid": mid},
    ).all()

    noise_rows     = [r for r in all_rows if r.noise]
    not_noise_rows = [r for r in all_rows if not r.noise]

    sampled_noise     = _stable_sample(noise_rows,     n, seed,     label="noise=True")
    sampled_not_noise = _stable_sample(not_noise_rows, n, seed ^ 1, label="noise=False")
    sampled = sorted(sampled_noise + sampled_not_noise, key=lambda r: r.id)

    rows = []
    for i, row in enumerate(sampled, start=1):
        rows.append({
            "sample_id":               f"noise-{i:04d}",
            "message_pk":              row.id,
            "message_header_hash":     hash_fn(row.message_id_header),
            "sender_domain":           _sender_domain(row.sender_email),
            "noise":                   row.noise,
            "sensitivity":             ",".join(sorted(row.sensitivity or [])),
            "has_owner_reply_in_thread": row.thread_id in owner_threads,
            "thread_message_count":    thread_counts.get(row.thread_id, 0),
            "subject_chars":           len(row.subject or ""),
            "clean_text_chars":        len(row.clean_text or ""),
            "date_day":                _date_day(row.ts),
        })
    return rows, len(noise_rows), len(not_noise_rows)


# ── sensitivity samples ───────────────────────────────────────────────────────

def sample_sensitivity(
    session, mailbox_id: str, n: int, seed: int, hash_fn
) -> tuple[list[dict], int]:
    """Return (rows, denominator). Samples messages with non-default sensitivity."""
    all_rows = session.execute(
        text(
            "SELECT id, message_id_header, sender_email, sensitivity, noise, "
            "subject, clean_text, ts "
            "FROM message WHERE mailbox_id = :mid "
            "AND NOT (sensitivity = ARRAY['none']::text[]) ORDER BY id"
        ),
        {"mid": mailbox_id},
    ).all()

    sampled = _stable_sample(all_rows, n, seed ^ 2, label="sensitivity")
    rows = []
    for i, row in enumerate(sorted(sampled, key=lambda r: r.id), start=1):
        rows.append({
            "sample_id":           f"sens-{i:04d}",
            "message_pk":          row.id,
            "message_header_hash": hash_fn(row.message_id_header),
            "sender_domain":       _sender_domain(row.sender_email),
            "sensitivity":         ",".join(sorted(row.sensitivity or [])),
            "noise":               row.noise,
            "subject_chars":       len(row.subject or ""),
            "clean_text_chars":    len(row.clean_text or ""),
            "date_day":            _date_day(row.ts),
        })
    return rows, len(all_rows)


# ── identity samples ──────────────────────────────────────────────────────────

def sample_identity(
    session, mailbox_id: str, n: int, seed: int
) -> tuple[list[dict], int]:
    """Return (rows, denominator)."""
    all_rows = session.execute(
        text(
            "SELECT p.id, p.canonical_email, p.role, p.role_confidence, "
            "COUNT(i.email) AS identity_count, "
            "COALESCE(SUM(COALESCE(array_length(i.display_names, 1), 0)), 0) AS display_name_count, "
            "COALESCE(e.message_count, 0) AS message_count "
            "FROM person p "
            "LEFT JOIN identity i ON i.person_id = p.id AND i.mailbox_id = :mid "
            "LEFT JOIN edge e ON e.person_id = p.id AND e.mailbox_id = :mid "
            "WHERE p.mailbox_id = :mid "
            "GROUP BY p.id, p.canonical_email, p.role, p.role_confidence, e.message_count "
            "ORDER BY p.id"
        ),
        {"mid": mailbox_id},
    ).all()

    sampled = _stable_sample(all_rows, n, seed ^ 3, label="identity")
    rows = []
    for row in sorted(sampled, key=lambda r: r.id):
        rows.append({
            "person_id":             row.id,
            "canonical_email_domain": _sender_domain(row.canonical_email),
            "identity_count":        row.identity_count or 0,
            "display_name_count":    row.display_name_count or 0,
            "message_count":         row.message_count or 0,
            "role":                  row.role,
            "role_confidence":       round(float(row.role_confidence or 0), 4),
        })
    return rows, len(all_rows)


# ── edge samples ──────────────────────────────────────────────────────────────

def sample_edges(
    session, mailbox_id: str, n: int, seed: int
) -> tuple[list[dict], int]:
    """Return (rows, denominator)."""
    all_rows = session.execute(
        text(
            "SELECT e.person_id, p.role, e.weight, e.message_count, e.last_contact "
            "FROM edge e JOIN person p ON p.id = e.person_id "
            "WHERE e.mailbox_id = :mid ORDER BY e.person_id"
        ),
        {"mid": mailbox_id},
    ).all()

    sampled = _stable_sample(all_rows, n, seed ^ 4, label="edges")
    rows = []
    for row in sorted(sampled, key=lambda r: r.person_id):
        rows.append({
            "person_id":  row.person_id,
            "role":       row.role,
            "weight":     round(float(row.weight or 0), 6),
            "msg_count":  row.message_count,
            "last_ts_day": _date_day(row.last_contact),
        })
    return rows, len(all_rows)


# ── thread samples ────────────────────────────────────────────────────────────

def sample_threads(
    session, mailbox_id: str, n: int, seed: int
) -> tuple[list[dict], int]:
    """Return (rows, denominator)."""
    all_rows = session.execute(
        text(
            "SELECT t.id, t.t_start, t.t_end, t.subject_norm, "
            "COALESCE(array_length(t.participants, 1), 0) AS participant_count, "
            "COALESCE(msg.message_count, 0) AS message_count, "
            "COALESCE(msg.noise_count, 0) AS noise_count, "
            "COALESCE(ts.sensitivity_set, ARRAY[]::text[]) AS sensitivity_set "
            "FROM thread t "
            "LEFT JOIN ("
            "  SELECT thread_id, COUNT(*) AS message_count, "
            "    COUNT(*) FILTER (WHERE noise) AS noise_count "
            "  FROM message WHERE mailbox_id = :mid GROUP BY thread_id"
            ") msg ON msg.thread_id = t.id "
            "LEFT JOIN ("
            "  SELECT m.thread_id, array_agg(DISTINCT s ORDER BY s) AS sensitivity_set "
            "  FROM message m, unnest(m.sensitivity) s "
            "  WHERE m.mailbox_id = :mid GROUP BY m.thread_id"
            ") ts ON ts.thread_id = t.id "
            "WHERE t.mailbox_id = :mid ORDER BY t.id"
        ),
        {"mid": mailbox_id},
    ).all()

    sampled = _stable_sample(all_rows, n, seed ^ 5, label="threads")
    rows = []
    for row in sorted(sampled, key=lambda r: r.id):
        rows.append({
            "thread_id":         row.id,
            "message_count":     row.message_count,
            "participant_count": row.participant_count,
            "noise_count":       row.noise_count,
            "sensitivity_set":   ",".join(sorted(row.sensitivity_set or [])),
            "t_start_day":       _date_day(row.t_start),
            "t_end_day":         _date_day(row.t_end),
            "subject_chars":     len(row.subject_norm or ""),
        })
    return rows, len(all_rows)


# ── label template ────────────────────────────────────────────────────────────

def build_label_template(
    noise_rows: list[dict], sens_rows: list[dict]
) -> list[dict]:
    """One row per unique message_pk; noise sample_ids take precedence."""
    seen: set[str] = set()
    template = []
    for row in sorted(noise_rows, key=lambda r: r["sample_id"]):
        seen.add(row["message_pk"])
        template.append(_blank_label_row(row["sample_id"]))
    for row in sorted(sens_rows, key=lambda r: r["sample_id"]):
        if row["message_pk"] not in seen:
            seen.add(row["message_pk"])
            template.append(_blank_label_row(row["sample_id"]))
    return template


def _blank_label_row(sample_id: str) -> dict:
    return {
        "sample_id":          sample_id,
        "actual_noise":       "",
        "actual_sensitivity": "",
        "project_relevant":   "",
        "notes":              "",
        "reviewed_by":        "",
        "reviewed_at":        "",
    }


# ── local review file ─────────────────────────────────────────────────────────

def emit_local_review_file(
    session, mailbox_id: str, noise_rows: list[dict], out_dir: Path
) -> None:
    _assert_under_local(out_dir)
    out_path = out_dir / "local_review.csv"
    print(
        "\n[PRIVACY WARNING] Writing raw message content to local review file.\n"
        "This file must NEVER be committed to the repository.\n"
        f"  Path: {out_path}\n"
    )
    pks = [r["message_pk"] for r in noise_rows]
    if not pks:
        print("No rows to write — skipping local review file.")
        return

    raw_rows = session.execute(
        select(orm.Message.id, orm.Message.subject, orm.Message.clean_text, orm.Message.ts)
        .where(orm.Message.id.in_(pks))
        .order_by(orm.Message.id)
    ).all()
    raw_map = {r.id: r for r in raw_rows}

    fieldnames = ["sample_id", "message_pk", "date_day", "subject", "clean_text_excerpt"]
    rows = []
    for sample_row in sorted(noise_rows, key=lambda r: r["sample_id"]):
        pk = sample_row["message_pk"]
        raw = raw_map.get(pk)
        rows.append({
            "sample_id":          sample_row["sample_id"],
            "message_pk":         pk,
            "date_day":           sample_row["date_day"],
            "subject":            raw.subject if raw else "",
            "clean_text_excerpt": (raw.clean_text or "")[:500] if raw else "",
        })
    _write_csv(out_path, fieldnames, rows)


# ── main ──────────────────────────────────────────────────────────────────────

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate privacy-safe quality reports from an ingested mailbox."
    )
    p.add_argument("--mailbox-id", required=True, help="UUID of the mailbox to inspect.")
    p.add_argument("--out", default=DEFAULT_OUT, help="Output directory (default: .local/reports/live-quality).")
    p.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE, metavar="N",
                   help="Per-bucket sample size (default: 50).")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help="RNG seed for deterministic sampling (default: 42).")
    p.add_argument("--include-sensitive", action="store_true",
                   help="Include sensitivity-tagged messages in noise samples (default: off).")
    p.add_argument("--hash-ids", action=argparse.BooleanOptionalAction, default=True,
                   help="Hash message headers and email addresses in output (default: on).")
    p.add_argument("--emit-local-review-file", action="store_true",
                   help="Write a local-only raw review CSV (never commit this). "
                        "Hard-fails if --out is not under .local/.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out_dir = Path(args.out)

    if args.emit_local_review_file:
        _assert_under_local(out_dir)

    session = SessionLocal()
    try:
        mailbox = session.get(orm.Mailbox, args.mailbox_id)
        if mailbox is None:
            sys.exit(f"ERROR: mailbox {args.mailbox_id!r} not found.")
        owner_email = mailbox.owner_email
        mid = args.mailbox_id

        def hash_fn(value: str) -> str:
            return _hash_id(value, mid) if args.hash_ids else value

        n = args.sample_size
        seed = args.seed

        print(f"Mailbox : {mid}")
        print(f"Owner   : {owner_email}")
        print(f"Out     : {out_dir}")
        print(f"Seed    : {seed}  Sample-size: {n}/bucket\n")

        # summary
        summary = build_summary(session, mid, owner_email)
        _write_json(out_dir / "summary.json", summary)
        print(
            f"Messages : {summary['message_count']}  "
            f"Noise: {summary['noise_count']} ({summary['noise_pct']:.1%})  "
            f"Threads: {summary['thread_count']}  "
            f"People: {summary['person_count']}  Edges: {summary['edge_count']}"
        )

        # noise samples
        noise_rows, noise_denom, not_noise_denom = sample_noise(
            session, mid, owner_email, n, seed, hash_fn
        )
        _write_csv(
            out_dir / "noise_samples.csv",
            ["sample_id", "message_pk", "message_header_hash", "sender_domain",
             "noise", "sensitivity", "has_owner_reply_in_thread",
             "thread_message_count", "subject_chars", "clean_text_chars", "date_day"],
            noise_rows,
        )
        print(f"noise_samples.csv    : {len(noise_rows)} rows  "
              f"(noise={noise_denom} / not-noise={not_noise_denom})")

        # sensitivity samples
        sens_rows, sens_denom = sample_sensitivity(session, mid, n, seed, hash_fn)
        _write_csv(
            out_dir / "sensitivity_samples.csv",
            ["sample_id", "message_pk", "message_header_hash", "sender_domain",
             "sensitivity", "noise", "subject_chars", "clean_text_chars", "date_day"],
            sens_rows,
        )
        print(f"sensitivity_samples.csv: {len(sens_rows)} rows  (denominator={sens_denom})")

        # identity samples
        identity_rows, identity_denom = sample_identity(session, mid, n, seed)
        _write_csv(
            out_dir / "identity_samples.csv",
            ["person_id", "canonical_email_domain", "identity_count",
             "display_name_count", "message_count", "role", "role_confidence"],
            identity_rows,
        )
        print(f"identity_samples.csv : {len(identity_rows)} rows  (denominator={identity_denom})")

        # edge samples
        edge_rows, edge_denom = sample_edges(session, mid, n, seed)
        _write_csv(
            out_dir / "edge_samples.csv",
            ["person_id", "role", "weight", "msg_count", "last_ts_day"],
            edge_rows,
        )
        print(f"edge_samples.csv     : {len(edge_rows)} rows  (denominator={edge_denom})")

        # thread samples
        thread_rows, thread_denom = sample_threads(session, mid, n, seed)
        _write_csv(
            out_dir / "thread_samples.csv",
            ["thread_id", "message_count", "participant_count", "noise_count",
             "sensitivity_set", "t_start_day", "t_end_day", "subject_chars"],
            thread_rows,
        )
        print(f"thread_samples.csv   : {len(thread_rows)} rows  (denominator={thread_denom})")

        # label template
        label_rows = build_label_template(noise_rows, sens_rows)
        _write_csv(
            out_dir / "label_template.csv",
            ["sample_id", "actual_noise", "actual_sensitivity", "project_relevant",
             "notes", "reviewed_by", "reviewed_at"],
            label_rows,
        )
        print(f"label_template.csv   : {len(label_rows)} rows")

        # optional local review file
        if args.emit_local_review_file:
            emit_local_review_file(session, mid, noise_rows, out_dir)

        print(f"\nDone. Reports written to: {out_dir.resolve()}")

    finally:
        session.close()


if __name__ == "__main__":
    main()
