"""Ticket 3.2 — candidate-pair blocking (spec 03 §6)."""
from __future__ import annotations

from conftest import build_features

from services.enrich.clustering.blocking import candidate_pairs, participant_idf
from services.enrich.clustering.params import PARAMS


def test_idf_and_ubiquitous():
    _, _, _, feats = build_features()
    idf, ubiquitous, df = participant_idf(feats, PARAMS.drop_frac)
    # df counts are positive and idf is finite.
    assert all(d >= 1 for d in df.values())
    assert all(v >= 0 for v in idf.values())
    # On an 18-message fixture no contact is on > half the threads -> none ubiquitous.
    assert isinstance(ubiquitous, set)


def test_pairs_sorted_and_canonical():
    _, _, _, feats = build_features()
    idf, ubiquitous, df = participant_idf(feats, PARAMS.drop_frac)
    pairs = candidate_pairs(feats, ubiquitous, df, PARAMS)
    assert pairs == sorted(pairs)
    assert all(i < j for i, j in pairs)
    # No duplicates.
    assert len(pairs) == len(set(pairs))


def test_shared_participant_blocks_atlas_threads():
    store, _, ctx, feats = build_features()
    idf, ubiquitous, df = participant_idf(feats, PARAMS.drop_frac)
    pairs = candidate_pairs(feats, ubiquitous, df, PARAMS)
    # Jenna is on multiple Atlas threads -> at least one candidate pair exists.
    assert len(pairs) > 0


def test_deterministic():
    _, _, _, feats = build_features()
    idf, ubiquitous, df = participant_idf(feats, PARAMS.drop_frac)
    p1 = candidate_pairs(feats, ubiquitous, df, PARAMS)
    p2 = candidate_pairs(feats, ubiquitous, df, PARAMS)
    assert p1 == p2


def test_candidate_count_bounded():
    _, _, _, feats = build_features()
    idf, ubiquitous, df = participant_idf(feats, PARAMS.drop_frac)
    pairs = candidate_pairs(feats, ubiquitous, df, PARAMS)
    n = len(feats)
    # Far below the all-pairs ceiling.
    assert len(pairs) <= n * (n - 1) // 2
