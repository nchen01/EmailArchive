"""Overlapping-clustering metrics (spec 03 §18).

Clustering is overlapping, so ARI/NMI are invalid. We use:
- ``ext_bcubed`` — extended BCubed P/R/F1 (Amigó et al. 2009), the primary metric.
- ``omega_index`` — agreement on co-membership counts (soft metric, decision C).
- ``pairwise_f1`` — sanity baseline.

All take ``dict[item -> set(labels)]`` over the same item set.
"""
from __future__ import annotations

from itertools import combinations


def ext_bcubed(gold: dict, pred: dict):
    items = sorted(gold)

    def mult(c, x, y):
        return len(c[x] & c[y])

    P = R = 0.0
    for x in items:
        pt, rt = [], []
        for y in items:
            mp = mult(pred, x, y)
            mg = mult(gold, x, y)
            if mp > 0:
                pt.append(min(mg, mp) / mp)
            if mg > 0:
                rt.append(min(mg, mp) / mg)
        P += sum(pt) / len(pt) if pt else 0.0
        R += sum(rt) / len(rt) if rt else 0.0
    n = len(items)
    P /= n
    R /= n
    F = 2 * P * R / (P + R) if (P + R) else 0.0
    return round(P, 3), round(R, 3), round(F, 3)


def omega_index(gold: dict, pred: dict):
    """Omega index: chance-corrected agreement on # of shared clusters per pair."""
    items = sorted(gold)
    pairs = list(combinations(items, 2))
    if not pairs:
        return 1.0

    def shared(c, x, y):
        return len(c[x] & c[y])

    obs = sum(1 for x, y in pairs if shared(gold, x, y) == shared(pred, x, y))
    obs /= len(pairs)

    # Expected agreement: sum over j of P_gold(j)*P_pred(j), where j = #shared.
    from collections import Counter

    g_counts = Counter(shared(gold, x, y) for x, y in pairs)
    p_counts = Counter(shared(pred, x, y) for x, y in pairs)
    npairs = len(pairs)
    exp = sum(
        (g_counts.get(j, 0) / npairs) * (p_counts.get(j, 0) / npairs)
        for j in set(g_counts) | set(p_counts)
    )
    if exp >= 1.0:
        return 1.0
    return round((obs - exp) / (1.0 - exp), 3)


def pairwise_f1(gold: dict, pred: dict):
    """Pairwise precision/recall/F1: a pair is 'together' if it shares >=1 label."""
    items = sorted(gold)
    pairs = list(combinations(items, 2))

    def together(c, x, y):
        return len(c[x] & c[y]) > 0

    tp = fp = fn = 0
    for x, y in pairs:
        g = together(gold, x, y)
        p = together(pred, x, y)
        if g and p:
            tp += 1
        elif p and not g:
            fp += 1
        elif g and not p:
            fn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return round(prec, 3), round(rec, 3), round(f, 3)
