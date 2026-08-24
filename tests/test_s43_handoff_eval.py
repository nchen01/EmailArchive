"""S43 - handoff quality evaluation harness tests.

DB-free: corpus coherence (every gold citation points at a real seeded header) and
the pure metric function. DB-gated: run the real generator over the corpus and prove
the hard gates hold - no-citation/no-claim, citations in evidence, and
sensitive/noise exclusions never leak - and that known limitations are reported
without failing.
"""
from __future__ import annotations

import os

import pytest

from services.handoff.eval.corpus import (
    check_scenario_coherence,
    load_corpus,
    scenario_defined_headers,
)
from services.handoff.eval.harness import evaluate

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
    not _db_reachable(), reason="DATABASE_URL not set or Postgres unreachable."
)


# ── DB-free: corpus coherence + pure metrics ─────────────────────────────────

def test_corpus_has_scenarios():
    corpus = load_corpus()
    assert len(corpus) >= 3  # first pass ships 5
    names = {s.name for s in corpus}
    assert len(names) == len(corpus)  # unique names


def test_every_gold_citation_points_to_a_real_seeded_header():
    for s in load_corpus():
        problems = check_scenario_coherence(s.data)
        assert problems == [], f"{s.name}: {problems}"
        # explicit: every excluded header is a real seeded message too
        headers = scenario_defined_headers(s.data)
        for h in s.data["gold"].get("excluded_headers", []):
            assert h in headers


def test_evaluate_pure_hard_gates_and_precision():
    data = {
        "name": "unit",
        "gold": {
            "project_labels": ["Nexus Auth Platform"],
            "decisions": [{"contains": "shipped the cutover", "cites": ["a@x"]}],
            "open_loops": [{"contains": "rotate keys", "cites": ["b@x"]}],
            "blockers": [],
            "stakeholders": ["acme.dev"],
            "excluded_headers": ["sens@x"],
            "stale_conflict": False,
        },
    }
    generated = {
        "claims": [
            {"kind": "decision", "text": "Shipped the cutover", "project_label": "Nexus Auth Platform", "cites": ["a@x"]},
            {"kind": "open_loop", "text": "Rotate keys", "project_label": "Nexus Auth Platform", "cites": ["b@x"]},
        ],
        "evidence": [
            {"header": "a@x", "sender_domain": "acme.dev", "sender_display": "A", "subject": "s", "body": "b"},
            {"header": "b@x", "sender_domain": "acme.dev", "sender_display": "B", "subject": "s", "body": "b"},
        ],
        "exclusions": [],
    }
    r = evaluate(data, generated)
    assert r.hard_pass is True
    assert r.quality["decisions_found"] == 1 and r.quality["open_loops_found"] == 1
    assert r.quality["missing_decisions"] == [] and r.quality["missing_open_loops"] == []
    assert r.quality["project_labels_present"] is True
    assert r.quality["stakeholders_present"] is True
    assert r.quality["claim_precision_proxy"] == 1.0
    assert r.limitations == []


def test_evaluate_flags_excluded_leak_and_uncited_claim():
    data = {"name": "unit2", "gold": {"excluded_headers": ["sens@x"]}}
    generated = {
        "claims": [
            {"kind": "decision", "text": "leaky", "project_label": None, "cites": ["sens@x"]},
            {"kind": "decision", "text": "uncited", "project_label": None, "cites": []},
        ],
        "evidence": [{"header": "sens@x", "sender_domain": "x", "sender_display": "", "subject": "", "body": ""}],
        "exclusions": [],
    }
    r = evaluate(data, generated)
    assert r.hard_gates["excluded_material_absent"] is False  # sens@x leaked
    assert r.hard_gates["every_claim_cited"] is False          # empty citation
    assert r.hard_pass is False


def test_evaluate_reports_limitations_without_failing():
    data = {
        "name": "unit3",
        "gold": {"blockers": [{"contains": "blocked on x", "cites": ["a@x"]}], "stale_conflict": True,
                 "excluded_headers": []},
    }
    generated = {
        "claims": [{"kind": "open_loop", "text": "Blocked on X", "project_label": None, "cites": ["a@x"]}],
        "evidence": [{"header": "a@x", "sender_domain": "x", "sender_display": "", "subject": "", "body": ""}],
        "exclusions": [],
    }
    r = evaluate(data, generated)
    assert r.hard_pass is True                       # limitations are NOT hard failures
    assert r.quality["blockers_found"] == 1          # content present (as open_loop)
    assert len(r.limitations) == 2                   # blocker-kind + stale/conflict


# ── DB-gated: run the real generator over the corpus ─────────────────────────

def _fresh():
    from services.db.engine import SessionLocal
    return SessionLocal()


@requires_db
def test_all_scenarios_pass_hard_gates():
    from services.handoff.eval.harness import run_scenario
    s = _fresh()
    try:
        for sc in load_corpus():
            r = run_scenario(s, sc.data)
            assert r.hard_pass, f"{sc.name} hard gates: {r.hard_gates}"
            # no-citation/no-claim + citations resolve in-package
            assert r.hard_gates["every_claim_cited"] is True
            assert r.hard_gates["citations_in_evidence"] is True
    finally:
        s.close()


@requires_db
def test_excluded_material_never_appears():
    from services.handoff.eval.harness import (
        cleanup,
        collect,
        run_scenario,
        seed_scenario,
    )
    from services.handoff.generator import generate_candidate
    from services.db import models as orm

    s = _fresh()
    try:
        for sc in load_corpus():
            excluded = set(sc.data["gold"].get("excluded_headers", []))
            if not excluded:
                # still verify the gate holds trivially
                assert run_scenario(s, sc.data).hard_gates["excluded_material_absent"]
                continue
            mid, pkg_id = seed_scenario(s, sc.data)
            try:
                generate_candidate(s, s.get(orm.HandoffPackage, pkg_id))
                s.commit()
                g = collect(s, pkg_id)
                headers = {e["header"] for e in g["evidence"]}
                cites = {h for c in g["claims"] for h in c["cites"]}
                assert not (excluded & headers), f"{sc.name}: excluded header in evidence"
                assert not (excluded & cites), f"{sc.name}: excluded header cited by a claim"
            finally:
                cleanup(s, mid, pkg_id)
    finally:
        s.close()


@requires_db
def test_expected_decisions_and_open_loops_found():
    from services.handoff.eval.harness import run_scenario
    s = _fresh()
    try:
        for sc in load_corpus():
            r = run_scenario(s, sc.data)
            assert r.quality["missing_decisions"] == [], f"{sc.name}: {r.quality['missing_decisions']}"
            assert r.quality["missing_open_loops"] == [], f"{sc.name}: {r.quality['missing_open_loops']}"
            assert r.quality["missing_project_labels"] == [], f"{sc.name}: {r.quality['missing_project_labels']}"
    finally:
        s.close()


@requires_db
def test_runner_is_deterministic():
    from services.handoff.eval.harness import run_scenario
    from services.handoff.eval.report import to_json
    sc = next(s for s in load_corpus() if s.name == "engineering_project")
    s = _fresh()
    try:
        a = to_json(run_scenario(s, sc.data))
        b = to_json(run_scenario(s, sc.data))
    finally:
        s.close()
    assert a == b
