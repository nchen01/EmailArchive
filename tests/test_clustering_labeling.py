"""Ticket 3.8 — labeling.py (c-TF-IDF + fallback + sticky renames, spec 03 §12)."""
from __future__ import annotations

from conftest import build_features

from services.enrich.clustering.blocking import candidate_pairs, participant_idf
from services.enrich.clustering.communities import (
    community_profiles,
    detect_communities,
    soft_assign,
)
from services.enrich.clustering.graph import build_edges
from services.enrich.clustering.labeling import cluster_signature, label_projects
from services.enrich.clustering.materialize import materialize
from services.enrich.clustering.params import ClusteringParams

PARAMS = ClusteringParams(
    tau=0.12, w_part=0.30, w_emb=0.10, w_kw=0.45, w_temp=0.10, w_attach=0.05,
    ratio=0.2, min_aff=0.10,
)


def _projects(params=PARAMS):
    store, _, ctx, feats = build_features()
    idf, ub, df = participant_idf(feats, params.drop_frac)
    edges = build_edges(feats, candidate_pairs(feats, ub, df, params), idf, params)
    member, mod = detect_communities(len(feats), edges, params)
    prof = community_profiles(feats, member)
    assigns = soft_assign(feats, member, prof, idf, params)
    projects, assignments, _ = materialize(feats, assigns)
    return feats, projects


def test_all_projects_labeled():
    feats, projects = _projects()
    labeled = label_projects(feats, projects)
    for p in labeled:
        assert p["label"]
        assert p["label_source"] in {"ctfidf", "entity", "fallback", "user"}


def test_labels_deterministic():
    feats, p1 = _projects()
    _, p2 = _projects()
    l1 = {p["id"]: p["label"] for p in label_projects(feats, p1)}
    l2 = {p["id"]: p["label"] for p in label_projects(feats, p2)}
    assert l1 == l2


def test_atlas_or_borealis_surfaces_in_a_label():
    feats, projects = _projects()
    labeled = label_projects(feats, projects)
    text = " ".join(p["label"].lower() for p in labeled)
    assert "atlas" in text or "borealis" in text or "renewal" in text


def test_signature_stable_and_order_independent():
    assert cluster_signature(["t1", "t2"]) == cluster_signature(["t2", "t1"])
    assert cluster_signature(["t1"]) != cluster_signature(["t2"])


def test_sticky_user_rename_applied_on_high_jaccard():
    feats, projects = _projects()
    # Build an override that matches one project's threads exactly.
    target = max(projects, key=lambda p: len(p["thread_ids"]))
    sig = cluster_signature(target["thread_ids"])
    overrides = {sig: ("My Custom Name", list(target["thread_ids"]))}
    labeled = label_projects(feats, projects, overrides=overrides)
    renamed = next(p for p in labeled if p["id"] == target["id"])
    assert renamed["label"] == "My Custom Name"
    assert renamed["label_source"] == "user"


def test_override_ignored_on_low_jaccard():
    feats, projects = _projects()
    overrides = {"deadbeef": ("Ghost", ["nonexistent-thread-xyz"])}
    labeled = label_projects(feats, projects, overrides=overrides)
    assert all(p["label"] != "Ghost" for p in labeled)
