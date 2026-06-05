"""Ticket 3.1 — ThreadFeatures + TF-IDF + owner exclusion (spec 03 §5)."""
from __future__ import annotations

import numpy as np
from conftest import build_features


def test_one_feature_per_nonempty_thread():
    store, _, ctx, feats = build_features()
    # Every thread that has messages yields exactly one feature.
    non_empty = {t.id for t in store.threads if ctx["messages_by_thread"].get(t.id)}
    assert {f.thread_id for f in feats} == non_empty


def test_owner_excluded_from_participants():
    _, _, ctx, feats = build_features()
    owner = ctx["owner_person_id"]
    for f in feats:
        assert owner not in f.participants
        assert owner not in f.msg_count_by_person


def test_participants_are_person_ids_of_real_contacts():
    _, _, ctx, feats = build_features()
    e2p = ctx["email_to_person_id"]
    # Atlas thread T1 (jenna, raj, alex) -> participants {jenna, raj}, owner stripped.
    by_tid = {f.thread_id: f for f in feats}
    # Find a thread whose participants include jenna and raj.
    jenna, raj = e2p["jenna@acme.com"], e2p["raj@acme.com"]
    assert any(jenna in f.participants and raj in f.participants for f in feats)
    assert by_tid  # non-empty


def test_embeddings_normalized_and_float32():
    _, _, _, feats = build_features(dim=64)
    for f in feats:
        assert f.embedding.dtype == np.float32
        assert f.embedding.shape == (64,)
        assert abs(float(np.linalg.norm(f.embedding)) - 1.0) < 1e-4


def test_keywords_present_from_tfidf_or_ner():
    _, _, _, feats = build_features()
    # At least one Atlas/Borealis thread surfaces a salient keyword.
    all_kw = set().union(*(set(f.keywords) for f in feats))
    assert any("atlas" in k or "borealis" in k for k in all_kw)


def test_attachment_hashes_captured():
    _, _, _, feats = build_features()
    # The Atlas cutover plan sha appears on >=1 thread.
    sha = "18e64564df12114fac07bbd3f6689d33305194e111a7fe5407742bf3b9df27f0"
    assert any(sha in f.attachment_hashes for f in feats)


def test_deterministic_feature_order():
    _, _, _, f1 = build_features()
    _, _, _, f2 = build_features()
    assert [f.thread_id for f in f1] == [f.thread_id for f in f2]
    assert [f.thread_id for f in f1] == sorted(f.thread_id for f in f1)
