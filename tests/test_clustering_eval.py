"""Ticket 3.10 — eval gates on the fixture (spec 03 §18, §22)."""
from __future__ import annotations

from services.enrich.clustering.eval.metrics import (
    ext_bcubed,
    omega_index,
    pairwise_f1,
)
from services.enrich.clustering.eval.run_eval import run


def test_metrics_perfect_agreement():
    gold = {"a": {"x"}, "b": {"x"}, "c": {"y"}}
    pred = {"a": {"1"}, "b": {"1"}, "c": {"2"}}
    p, r, f = ext_bcubed(gold, pred)
    assert (p, r, f) == (1.0, 1.0, 1.0)
    assert omega_index(gold, pred) == 1.0
    pp, pr, pf = pairwise_f1(gold, pred)
    assert pf == 1.0


def test_metrics_overlapping_multilabel():
    # Item b is in both x and y; pred matches.
    gold = {"a": {"x"}, "b": {"x", "y"}, "c": {"y"}}
    pred = {"a": {"1"}, "b": {"1", "2"}, "c": {"2"}}
    p, r, f = ext_bcubed(gold, pred)
    assert f == 1.0


def test_bcubed_penalizes_overmerge():
    gold = {"a": {"x"}, "b": {"y"}}
    pred = {"a": {"1"}, "b": {"1"}}  # wrongly merged
    p, r, f = ext_bcubed(gold, pred)
    assert p < 1.0


def test_eval_hard_gates_pass():
    summary = run(verbose=False)
    assert summary["gates"]["bcubed_f1>=0.75"], summary
    assert summary["gates"]["bcubed_precision>=0.80"], summary
    assert summary["gates"]["multi_gold>=0.80"], summary
    assert summary["passed"], summary


def test_eval_reports_omega():
    summary = run(verbose=False)
    # Omega is tracked (soft metric, not gated).
    assert "omega" in summary
    assert isinstance(summary["omega"], float)


def test_eval_deterministic():
    a = run(verbose=False)
    b = run(verbose=False)
    assert a == b
