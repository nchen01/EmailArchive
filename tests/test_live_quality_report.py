"""Unit tests for scripts/live_quality_report.py.

Covers: hashing/redaction, deterministic sampling, path safety,
empty-bucket handling, fewer-rows-than-sample-size warning, CSV/JSON
output determinism, and label template deduplication.

DB integration tests require DATABASE_URL (uses @requires_db).
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATABASE_URL = os.environ.get("DATABASE_URL")


def _db_reachable() -> bool:
    if not DATABASE_URL:
        return False
    try:
        from services.db.engine import engine
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres unreachable.",
)

# Import helpers under test (no DB required)
from scripts.live_quality_report import (  # noqa: E402
    DEFAULT_SEED,
    LOCAL_DIR,
    ROOT as SCRIPT_ROOT,
    _assert_under_local,
    _blank_label_row,
    _date_day,
    _hash_id,
    _sender_domain,
    _stable_sample,
    _write_csv,
    _write_json,
    build_label_template,
)


# ── _hash_id ──────────────────────────────────────────────────────────────────

def test_hash_id_is_16_hex_chars():
    h = _hash_id("foo@example.com", "mbx-123")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_id_is_stable():
    assert _hash_id("foo@example.com", "mbx-123") == _hash_id("foo@example.com", "mbx-123")


def test_hash_id_differs_by_mailbox():
    assert _hash_id("foo@example.com", "mbx-1") != _hash_id("foo@example.com", "mbx-2")


def test_hash_id_does_not_contain_input():
    h = _hash_id("secret@corp.com", "any-mailbox")
    assert "secret" not in h
    assert "corp" not in h


# ── _sender_domain ────────────────────────────────────────────────────────────

def test_sender_domain_normal():
    assert _sender_domain("user@example.com") == "example.com"


def test_sender_domain_no_at():
    assert _sender_domain("noemail") == "noemail"


def test_sender_domain_multiple_at():
    # rsplit('@', 1) takes the last @
    assert _sender_domain("a@b@c.com") == "c.com"


# ── _date_day ─────────────────────────────────────────────────────────────────

def test_date_day_none():
    assert _date_day(None) == ""


def test_date_day_datetime():
    from datetime import datetime, timezone
    ts = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert _date_day(ts) == "2026-03-15"


def test_date_day_string():
    assert _date_day("2026-01-02T10:00:00") == "2026-01-02"


# ── _stable_sample ────────────────────────────────────────────────────────────

def test_stable_sample_deterministic():
    rows = list(range(100))
    a = _stable_sample(rows, 10, seed=42)
    b = _stable_sample(rows, 10, seed=42)
    assert a == b


def test_stable_sample_different_seeds_differ():
    rows = list(range(100))
    a = _stable_sample(rows, 10, seed=42)
    b = _stable_sample(rows, 10, seed=99)
    assert a != b


def test_stable_sample_returns_n_items():
    rows = list(range(100))
    assert len(_stable_sample(rows, 20, seed=1)) == 20


def test_stable_sample_fewer_rows_returns_all_and_warns():
    rows = list(range(5))
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = _stable_sample(rows, 10, seed=1, label="test-bucket")
    assert result == rows
    assert len(w) == 1
    assert "only 5 rows available" in str(w[0].message)
    assert "test-bucket" in str(w[0].message)


def test_stable_sample_empty_bucket_returns_empty_and_warns():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = _stable_sample([], 10, seed=1, label="empty")
    assert result == []
    assert len(w) == 1


def test_stable_sample_exact_n_no_warning():
    rows = list(range(10))
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = _stable_sample(rows, 10, seed=1)
    assert len(result) == 10
    assert len(w) == 0


def test_stable_sample_requires_presorted_input():
    # Caller is responsible for sorting. Verify that two differently-ordered
    # inputs with the same seed produce different results (order matters).
    rows_asc  = list(range(20))
    rows_desc = list(range(19, -1, -1))
    a = _stable_sample(rows_asc,  10, seed=7)
    b = _stable_sample(rows_desc, 10, seed=7)
    assert a != b


# ── _assert_under_local ───────────────────────────────────────────────────────

def test_assert_under_local_accepts_local_path():
    safe = LOCAL_DIR / "reports" / "test"
    _assert_under_local(safe)  # must not raise


def test_assert_under_local_rejects_repo_root(tmp_path):
    with pytest.raises(SystemExit) as exc:
        _assert_under_local(SCRIPT_ROOT / "reports")
    assert "not under" in str(exc.value)


def test_assert_under_local_rejects_tmp(tmp_path):
    with pytest.raises(SystemExit):
        _assert_under_local(tmp_path)


# ── _write_csv ────────────────────────────────────────────────────────────────

def test_write_csv_deterministic(tmp_path):
    out = tmp_path / "test.csv"
    rows = [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
    _write_csv(out, ["a", "b"], rows)
    content1 = out.read_bytes()
    out.unlink()
    _write_csv(out, ["a", "b"], rows)
    content2 = out.read_bytes()
    assert content1 == content2


def test_write_csv_uses_lf_newlines(tmp_path):
    out = tmp_path / "test.csv"
    _write_csv(out, ["x"], [{"x": "v"}])
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    assert b"\n" in raw


def test_write_csv_no_timestamps_in_header(tmp_path):
    out = tmp_path / "test.csv"
    _write_csv(out, ["a", "b"], [{"a": "1", "b": "2"}])
    content = out.read_text()
    # Header must be exactly the fieldnames we passed
    first_line = content.split("\n")[0]
    assert first_line == "a,b"


# ── _write_json ───────────────────────────────────────────────────────────────

def test_write_json_sort_keys(tmp_path):
    out = tmp_path / "test.json"
    _write_json(out, {"z": 1, "a": 2, "m": 3})
    content = out.read_text()
    parsed = json.loads(content)
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_write_json_deterministic(tmp_path):
    out = tmp_path / "test.json"
    data = {"noise_count": 5, "message_count": 10, "graph_density": 0.1}
    _write_json(out, data)
    c1 = out.read_bytes()
    out.unlink()
    _write_json(out, data)
    c2 = out.read_bytes()
    assert c1 == c2


def test_write_json_no_timestamps_in_output(tmp_path):
    out = tmp_path / "test.json"
    _write_json(out, {"count": 42})
    content = out.read_text()
    # Verify no auto-added timestamp keys
    parsed = json.loads(content)
    assert "generated_at" not in parsed
    assert "timestamp" not in parsed


def test_write_json_ends_with_newline(tmp_path):
    out = tmp_path / "test.json"
    _write_json(out, {"x": 1})
    assert out.read_bytes().endswith(b"\n")


# ── build_label_template ──────────────────────────────────────────────────────

def _make_noise_row(sample_id, pk):
    return {"sample_id": sample_id, "message_pk": pk, "date_day": "2026-01-01"}


def _make_sens_row(sample_id, pk):
    return {"sample_id": sample_id, "message_pk": pk, "date_day": "2026-01-01"}


def test_label_template_deduplicated_by_message_pk():
    noise = [_make_noise_row("noise-0001", "pk-A"), _make_noise_row("noise-0002", "pk-B")]
    sens  = [_make_sens_row("sens-0001", "pk-A")]  # pk-A also in noise
    rows = build_label_template(noise, sens)
    pks = [r["sample_id"] for r in rows]
    # pk-A should appear only once, under its noise sample_id
    assert pks.count("noise-0001") == 1
    assert "sens-0001" not in pks


def test_label_template_sens_only_row_included():
    noise = [_make_noise_row("noise-0001", "pk-A")]
    sens  = [_make_sens_row("sens-0001", "pk-B")]  # different pk
    rows = build_label_template(noise, sens)
    sample_ids = {r["sample_id"] for r in rows}
    assert "noise-0001" in sample_ids
    assert "sens-0001" in sample_ids


def test_label_template_blank_fields():
    noise = [_make_noise_row("noise-0001", "pk-X")]
    rows = build_label_template(noise, [])
    assert rows[0]["actual_noise"] == ""
    assert rows[0]["actual_sensitivity"] == ""
    assert rows[0]["project_relevant"] == ""
    assert rows[0]["reviewed_by"] == ""
    assert rows[0]["reviewed_at"] == ""


def test_label_template_deterministic():
    noise = [_make_noise_row(f"noise-{i:04d}", f"pk-{i}") for i in range(5)]
    sens  = [_make_sens_row(f"sens-{i:04d}", f"pk-{i+10}") for i in range(3)]
    a = build_label_template(noise, sens)
    b = build_label_template(noise, sens)
    assert a == b


def test_label_template_empty_inputs():
    assert build_label_template([], []) == []


# ── DB integration: full report run ──────────────────────────────────────────

@requires_db
def test_full_report_runs_on_fixture_mailbox(tmp_path):
    """Full report pipeline runs without error on the fixture mailbox."""
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import persist_l0, persist_l1
    from services.ingest.pipeline import IngestConfig, run_ingest
    from services.enrich.pipeline import run_enrichment
    from scripts.live_quality_report import main

    FIXTURE = ROOT / "fixtures" / "mailbox.json"
    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=FIXTURE,
        owner_email="report-test@example.com",
        internal_domains=["example.com"],
    )
    store = run_ingest(cfg)
    enrich = run_enrichment(store.messages, owner_email="report-test@example.com",
                            internal_domains=["example.com"])

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email="report-test@example.com",
        embed_model="t", embed_dim=0, config={},
    )
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)

    try:
        persist_l0(store, mid, session)
        persist_l1(enrich, mid, session)

        out_dir = tmp_path / ".local" / "reports" / "test"
        main([
            "--mailbox-id", mid,
            "--out", str(out_dir),
            "--sample-size", "5",
            "--seed", "42",
        ])

        assert (out_dir / "summary.json").exists()
        assert (out_dir / "noise_samples.csv").exists()
        assert (out_dir / "sensitivity_samples.csv").exists()
        assert (out_dir / "identity_samples.csv").exists()
        assert (out_dir / "edge_samples.csv").exists()
        assert (out_dir / "thread_samples.csv").exists()
        assert (out_dir / "label_template.csv").exists()

        summary = json.loads((out_dir / "summary.json").read_text())
        assert summary["message_count"] > 0
        assert "generated_at" not in summary
        assert "timestamp" not in summary

    finally:
        from sqlalchemy import delete
        session.execute(delete(orm.Mailbox).where(orm.Mailbox.id == mid))
        session.commit()
        session.close()


@requires_db
def test_report_is_deterministic_on_fixture_mailbox(tmp_path):
    """Same seed produces byte-identical output on two consecutive runs."""
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import persist_l0, persist_l1
    from services.ingest.pipeline import IngestConfig, run_ingest
    from services.enrich.pipeline import run_enrichment
    from scripts.live_quality_report import main

    FIXTURE = ROOT / "fixtures" / "mailbox.json"
    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=FIXTURE,
        owner_email="determ-test@example.com",
        internal_domains=["example.com"],
    )
    store = run_ingest(cfg)
    enrich = run_enrichment(store.messages, owner_email="determ-test@example.com",
                            internal_domains=["example.com"])

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email="determ-test@example.com",
        embed_model="t", embed_dim=0, config={},
    )
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)

    try:
        persist_l0(store, mid, session)
        persist_l1(enrich, mid, session)

        out1 = tmp_path / "run1"
        out2 = tmp_path / "run2"
        args = ["--mailbox-id", mid, "--sample-size", "5", "--seed", "42"]
        main(args + ["--out", str(out1)])
        main(args + ["--out", str(out2)])

        for fname in ["summary.json", "noise_samples.csv", "label_template.csv"]:
            assert (out1 / fname).read_bytes() == (out2 / fname).read_bytes(), \
                f"{fname} is not byte-identical across two runs with the same seed"

    finally:
        from sqlalchemy import delete
        session.execute(delete(orm.Mailbox).where(orm.Mailbox.id == mid))
        session.commit()
        session.close()
