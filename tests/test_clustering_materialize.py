"""Ticket 3.7 — materialize.py + confidence.py (spec 03 §11, §13)."""
from __future__ import annotations

from collections import defaultdict

from conftest import build_features

from services.enrich.clustering.blocking import candidate_pairs, participant_idf
from services.enrich.clustering.communities import (
    community_profiles,
    detect_communities,
    soft_assign,
)
from services.enrich.clustering.confidence import project_confidence
from services.enrich.clustering.graph import build_edges
from services.enrich.clustering.materialize import _stable_project_id, materialize
from services.enrich.clustering.params import ClusteringParams

PARAMS = ClusteringParams(
    tau=0.12, w_part=0.30, w_emb=0.10, w_kw=0.45, w_temp=0.10, w_attach=0.05,
    ratio=0.2, min_aff=0.10,
)


def _materialize(params=PARAMS):
    store, _, ctx, feats = build_features()
    idf, ub, df = participant_idf(feats, params.drop_frac)
    edges = build_edges(feats, candidate_pairs(feats, ub, df, params), idf, params)
    member, mod = detect_communities(len(feats), edges, params)
    prof = community_profiles(feats, member)
    assigns = soft_assign(feats, member, prof, idf, params)
    projects, assignments, comm_to_pid = materialize(feats, assigns)
    return feats, prof, assigns, projects, assignments, comm_to_pid


def test_stable_project_id_deterministic():
    a = _stable_project_id(["t2", "t1"])
    b = _stable_project_id(["t1", "t2"])
    assert a == b  # order-independent
    assert _stable_project_id(["t1"]) != _stable_project_id(["t2"])


def test_every_thread_assigned():
    feats, *_, projects, assignments, _ = _materialize()
    assigned_tids = {a["thread_id"] for a in assignments}
    all_tids = {f.thread_id for f in feats}
    assert assigned_tids == all_tids  # invariant §2: no thread dropped


def test_each_project_nonempty_and_back_referenced():
    *_, projects, assignments, _ = _materialize()
    pid_to_threads = defaultdict(set)
    for a in assignments:
        pid_to_threads[a["project_id"]].add(a["thread_id"])
    for p in projects:
        assert p["thread_ids"]
        # every listed thread has an assignment back
        assert set(p["thread_ids"]) <= pid_to_threads[p["id"]]


def test_exactly_one_primary_per_thread():
    *_, assignments, _ = _materialize()
    by_thread = defaultdict(int)
    for a in assignments:
        if a["is_primary"]:
            by_thread[a["thread_id"]] += 1
    assert all(n == 1 for n in by_thread.values())


def test_members_sorted_by_involvement():
    *_, projects, _, _ = _materialize()
    for p in projects:
        invs = [m["involvement"] for m in p["members"]]
        assert invs == sorted(invs, reverse=True)
        assert p["member_ids"] == [m["person_id"] for m in p["members"]]


def test_confidence_bounds_and_determinism():
    feats, prof, assigns, projects, assignments, comm_to_pid = _materialize()
    pid_to_comm = {pid: c for c, pid in comm_to_pid.items()}
    by_pid = defaultdict(list)
    idx_by_tid = {f.thread_id: i for i, f in enumerate(feats)}
    for a in assignments:
        by_pid[a["project_id"]].append(idx_by_tid[a["thread_id"]])
    for p in projects:
        c = pid_to_comm[p["id"]]
        conf, dbg = project_confidence(feats, by_pid[p["id"]], prof, c, PARAMS)
        conf2, dbg2 = project_confidence(feats, by_pid[p["id"]], prof, c, PARAMS)
        assert 0.0 <= conf <= 1.0
        assert conf == conf2 and dbg == dbg2
        assert dbg["n_threads"] >= 1


def test_full_determinism():
    r1 = _materialize()
    r2 = _materialize()
    assert r1[3] == r2[3]  # projects
    assert r1[4] == r2[4]  # assignments
