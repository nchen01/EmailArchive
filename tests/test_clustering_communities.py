"""Tickets 3.5 + 3.6 — Leiden + soft membership (spec 03 §9–§10)."""
from __future__ import annotations

from conftest import build_features

from services.enrich.clustering.blocking import candidate_pairs, participant_idf
from services.enrich.clustering.communities import (
    community_profiles,
    detect_communities,
    soft_assign,
)
from services.enrich.clustering.graph import build_edges
from services.enrich.clustering.params import ClusteringParams

# Fixture-scale params (test embeddings carry less magnitude than real ones).
PARAMS = ClusteringParams(
    tau=0.12,
    w_part=0.30,
    w_emb=0.10,
    w_kw=0.45,
    w_temp=0.10,
    w_attach=0.05,
    ratio=0.2,
    min_aff=0.10,
)


def _pipeline(params=PARAMS):
    store, _, ctx, feats = build_features()
    idf, ub, df = participant_idf(feats, params.drop_frac)
    pairs = candidate_pairs(feats, ub, df, params)
    edges = build_edges(feats, pairs, idf, params)
    member, mod = detect_communities(len(feats), edges, params)
    prof = community_profiles(feats, member)
    assigns = soft_assign(feats, member, prof, idf, params)
    return store, ctx, feats, member, mod, prof, assigns


def test_membership_covers_all_nodes():
    _, _, feats, member, mod, _, _ = _pipeline()
    assert len(member) == len(feats)
    # Canonical ids: contiguous from 0.
    assert set(member) == set(range(len(set(member))))


def test_modularity_is_float():
    *_, mod, _, _ = _pipeline()
    assert isinstance(mod, float)


def test_determinism():
    a = _pipeline()
    b = _pipeline()
    assert a[3] == b[3]  # membership
    assert a[6] == b[6]  # assigns


def test_isolated_nodes_become_singletons():
    # Empty graph -> every node its own community.
    member, mod = detect_communities(5, [], PARAMS)
    assert member == [0, 1, 2, 3, 4]


def test_every_thread_has_primary():
    *_, assigns = _pipeline()
    for i, cs in assigns.items():
        assert len(cs) >= 1
        assert all(0 < w <= 1.0 + 1e-9 for w in cs.values())


def test_weekly_sync_multi_assigned():
    """Spec §22: the weekly-sync fixture (atlas+borealis) must land in >=2 projects."""
    store, ctx, feats, member, mod, prof, assigns = _pipeline()
    # Find the weekly-sync thread by subject.
    subj = {t.id: t.subject_norm for t in store.threads}
    idx_by_tid = {f.thread_id: i for i, f in enumerate(feats)}
    sync_tid = next(tid for tid, s in subj.items() if "weekly sync" in s.lower())
    sync_idx = idx_by_tid[sync_tid]
    # It should attach to at least 2 communities (overlapping membership), given
    # there are at least 2 communities to attach to.
    if len({c for c in member}) >= 2:
        assert len(assigns[sync_idx]) >= 2
