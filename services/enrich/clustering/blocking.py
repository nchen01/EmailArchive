"""Stage B — candidate-pair blocking (spec 03 §6).

All-pairs is O(n²). Block to ~O(n·k) candidates from two sources and union:
  (1) shared rare participant (inverted index, hub-capped by document frequency);
  (2) embedding ANN neighbours (catches subject-drift with no shared participant).

ANN backend: ``hnswlib`` when importable, otherwise a deterministic scikit-learn
``NearestNeighbors`` (cosine) fallback. The fallback is exact kNN, which is fine
at fixture/small-mailbox scale and removes a C-compiler build dependency.

Output is a *sorted list* of ``(i, j)`` pairs with ``i < j`` (decision H: no
unordered set iteration leaks downstream).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

import numpy as np

from .params import PARAMS, ClusteringParams


def participant_idf(feats, drop_frac: float = 0.5):
    """Return ``(idf, ubiquitous, df)``.

    ``idf[p]``  = log(N / df[p]); ``ubiquitous`` = participants on > drop_frac·N
    threads (no discriminative signal); ``df`` = document frequency per participant.
    """
    n = len(feats)
    df: Counter = Counter()
    for f in feats:
        for p in f.participants:
            df[p] += 1
    idf = {p: math.log(n / d) for p, d in df.items()}
    ubiquitous = {p for p, d in df.items() if d > drop_frac * n}
    return idf, ubiquitous, dict(df)


def _ann_neighbours(X: np.ndarray, ann_k: int) -> list[tuple[int, int]]:
    """Return sorted unique ``(i, j)`` ANN neighbour pairs (i < j)."""
    n = len(X)
    if n < 2:
        return []
    k = min(ann_k, n)
    pairs: set = set()

    try:
        import hnswlib

        idx = hnswlib.Index(space="cosine", dim=X.shape[1])
        idx.init_index(max_elements=n, ef_construction=200, M=16)
        idx.add_items(X, np.arange(n))
        idx.set_ef(max(ann_k * 2, 50))
        lab, _ = idx.knn_query(X, k=k)
    except ImportError:
        # Deterministic exact-kNN fallback (cosine == euclidean on L2-normed rows,
        # but ask for cosine explicitly for clarity).
        from sklearn.neighbors import NearestNeighbors

        nn = NearestNeighbors(n_neighbors=k, metric="cosine", algorithm="brute")
        nn.fit(X)
        lab = nn.kneighbors(X, return_distance=False)

    for i, row in enumerate(lab):
        for j in row:
            j = int(j)
            if i != j:
                pairs.add((min(i, j), max(i, j)))
    return sorted(pairs)


def candidate_pairs(
    feats,
    ubiquitous,
    df,
    params: ClusteringParams = PARAMS,
) -> list[tuple[int, int]]:
    ann_k = params.ann_k
    max_block = params.max_block
    pairs: set = set()

    # (1) shared rare participant via inverted index.
    inv: dict = defaultdict(list)
    for i, f in enumerate(feats):
        for p in sorted(f.participants):
            if p not in ubiquitous and df.get(p, 0) <= max_block:
                inv[p].append(i)
    for p in sorted(inv):
        ids = inv[p]
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                pairs.add((min(ids[a], ids[b]), max(ids[a], ids[b])))

    # (2) embedding ANN neighbours.
    if feats:
        X = np.vstack([f.embedding for f in feats]).astype("float32")
        pairs.update(_ann_neighbours(X, ann_k))

    return sorted(pairs)
