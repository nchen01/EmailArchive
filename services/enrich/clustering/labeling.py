"""Stage H — labeling (spec 03 §12).

No canonical project name exists, so we derive one with class-based TF-IDF
(c-TF-IDF): terms distinctive to a cluster vs. the corpus. Prefer a capitalized
entity, then top c-TF-IDF terms, then a "<top contact> · <month>" fallback.

Sticky user renames: keyed by a stable ``cluster_signature`` (sorted hash of the
high-weight thread_ids). On re-cluster, if an override's signature still matches a
new project at >= 0.5 Jaccard, reapply it with ``label_source="user"``.

LLM polish is intentionally omitted (off by default for determinism, §12).

Labels/sources are written onto the project dicts in place and returned.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict

from ekc_schemas import LabelSource


def cluster_signature(thread_ids: list[str]) -> str:
    key = ",".join(sorted(thread_ids))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _signature_threads(sig_to_threads: dict, sig: str) -> set:
    return sig_to_threads.get(sig, set())


def label_projects(feats, projects, overrides: dict | None = None):
    """Label every project. ``overrides`` maps ``cluster_signature -> (label, threads)``.

    For determinism the override match uses Jaccard of the override's stored
    thread set against the new project's thread set.
    """
    idx_by_tid = {f.thread_id: i for i, f in enumerate(feats)}

    # Build per-project keyword bags (primary cluster keywords).
    docs: dict = {}
    for p in projects:
        bag: list[str] = []
        for tid in p["thread_ids"]:
            if tid in idx_by_tid:
                bag.extend(feats[idx_by_tid[tid]].keywords)
        docs[p["id"]] = bag

    # c-TF-IDF: in how many clusters does each term appear.
    cdf: Counter = Counter()
    for bag in docs.values():
        for w in set(bag):
            cdf[w] += 1
    n_clusters = max(len(docs), 1)

    overrides = overrides or {}

    for p in projects:
        # Sticky user rename first.
        applied = _apply_override(p, overrides)
        if applied:
            continue

        tf = Counter(docs[p["id"]])
        score = {w: tf[w] * math.log((1 + n_clusters) / (1 + cdf[w])) for w in tf}
        # Deterministic ranking: score desc, then term asc.
        ranked = [w for w, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))]

        entity = next((w for w in ranked if w.istitle() or " " in w), None)
        if entity:
            p["label"] = entity.title()
            p["label_source"] = (
                LabelSource.ENTITY.value if entity in tf else LabelSource.CTFIDF.value
            )
        elif ranked:
            p["label"] = " · ".join(ranked[:2]).title()
            p["label_source"] = LabelSource.CTFIDF.value
        else:
            top = p["members"][0]["person_id"] if p.get("members") else "unknown"
            month = p["start"].strftime("%b %Y") if p.get("start") else ""
            p["label"] = f"{top} · {month}".strip(" ·")
            p["label_source"] = LabelSource.FALLBACK.value

    return projects


def _apply_override(p: dict, overrides: dict, j_thresh: float = 0.5) -> bool:
    if not overrides:
        return False
    new_set = set(p["thread_ids"])
    best_label, best_j = None, 0.0
    for _sig, (label, threads) in sorted(overrides.items()):
        old_set = set(threads)
        union = new_set | old_set
        j = len(new_set & old_set) / len(union) if union else 0.0
        if j > best_j:
            best_label, best_j = label, j
    if best_label is not None and best_j >= j_thresh:
        p["label"] = best_label
        p["label_source"] = LabelSource.USER.value
        return True
    return False
