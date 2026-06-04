"""Orchestrator — cluster_mailbox (spec 03 §15) + CLI + debug artifacts (§21).

``cluster_mailbox`` takes pre-built ``ThreadFeatures`` and returns a validated
``ClusteringResult`` (ekc_schemas). ``cluster_from_store`` is the convenience
entry that builds features first (used by the L1 pipeline and the eval).

Sparse-mailbox fallback (decision D / §17): below ``params.min_threads_leiden``
threads, skip blocking + Leiden and use agglomerative clustering on the full
dense similarity matrix.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

import numpy as np

from ekc_schemas import (
    ClusteringResult,
    LabelSource,
    Project,
    ProjectMember,
    ThreadProjectAssignment,
)

from .blocking import candidate_pairs, participant_idf
from .communities import community_profiles, detect_communities, soft_assign
from .confidence import project_confidence
from .features import build_thread_features
from .graph import build_edges
from .labeling import label_projects
from .labeling_tfidf import ThreadTfidf
from .materialize import materialize
from .params import PARAMS, ClusteringParams
from .similarity import thread_similarity


# ── hard-partition strategies ────────────────────────────────────────────────

def _dense_membership(feats, pidf, params: ClusteringParams):
    """Sparse-mailbox fallback: agglomerative clustering on the dense matrix.

    Builds the full pairwise *distance* matrix (1 - similarity) and runs
    scipy average-linkage agglomeration, cutting at ``1 - tau``. Deterministic.
    """
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    n = len(feats)
    if n <= 1:
        return [0] * n, 0.0

    dist = np.zeros((n, n), dtype="float64")
    for i in range(n):
        for j in range(i + 1, n):
            s = thread_similarity(feats[i], feats[j], pidf, params)
            d = 1.0 - s
            dist[i, j] = dist[j, i] = d

    condensed = squareform(dist, checks=False)
    z = linkage(condensed, method="average")
    # Cut so that merges only happen below distance (1 - tau), i.e. sim >= tau.
    labels = fcluster(z, t=1.0 - params.tau, criterion="distance")

    # Canonicalize to contiguous ids ordered by first member index.
    from .communities import _canonicalize

    return _canonicalize([int(x) for x in labels]), 0.0


def _leiden_membership(feats, pidf, params: ClusteringParams):
    _idf, ubiquitous, df = participant_idf(feats, params.drop_frac)
    pairs = candidate_pairs(feats, ubiquitous, df, params)
    edges = build_edges(feats, pairs, pidf, params)
    membership, modularity = detect_communities(len(feats), edges, params)
    return membership, modularity


# ── orchestrator ─────────────────────────────────────────────────────────────

def cluster_mailbox(
    feats,
    *,
    prev_result: ClusteringResult | None = None,
    overrides: dict | None = None,
    params: ClusteringParams = PARAMS,
    debug: bool = False,
) -> ClusteringResult:
    if not feats:
        return ClusteringResult(
            projects=[], assignments=[],
            run_meta=_run_meta(params, 0.0, 0, 0.0, "empty"),
        )

    pidf, _ubiquitous, _df = participant_idf(feats, params.drop_frac)

    # Strategy selection (decision D).
    if len(feats) < params.min_threads_leiden:
        membership, modularity = _dense_membership(feats, pidf, params)
        strategy = "agglomerative"
    else:
        membership, modularity = _leiden_membership(feats, pidf, params)
        strategy = "leiden"

    prof = community_profiles(feats, membership)
    assigns = soft_assign(feats, membership, prof, pidf, params)
    projects, assignments, comm_to_pid = materialize(feats, assigns)
    projects = label_projects(feats, projects, overrides=overrides)

    # Confidence: thread the community id through comm_to_pid.
    pid_to_comm = {pid: c for c, pid in comm_to_pid.items()}
    idx_by_tid = {f.thread_id: i for i, f in enumerate(feats)}
    by_pid: dict = defaultdict(list)
    for a in assignments:
        by_pid[a["project_id"]].append(idx_by_tid[a["thread_id"]])
    for p in projects:
        conf, dbg = project_confidence(feats, by_pid[p["id"]], prof, pid_to_comm[p["id"]], params)
        p["confidence"] = conf
        p["debug"] = dbg

    # ID carry-over on full re-cluster (remap assignments + by_pid too).
    if prev_result is not None:
        from .incremental import carry_over_ids

        prev = [pp.model_dump() for pp in prev_result.projects]
        # Snapshot old ids keyed by thread-set signature, carry over, then map.
        sig_to_old = {tuple(sorted(p["thread_ids"])): p["id"] for p in projects}
        carry_over_ids(projects, prev, params)
        id_map = {
            sig_to_old[tuple(sorted(p["thread_ids"]))]: p["id"] for p in projects
        }
        for a in assignments:
            a["project_id"] = id_map.get(a["project_id"], a["project_id"])
        assignments.sort(key=lambda a: (a["thread_id"], a["project_id"]))

    project_models = [
        Project(
            id=p["id"],
            label=p["label"],
            label_source=LabelSource(p["label_source"]),
            member_ids=p["member_ids"],
            members=[ProjectMember(**m) for m in p["members"]],
            thread_ids=p["thread_ids"],
            start=p["start"],
            end=p["end"],
            confidence=p["confidence"],
            debug=p.get("debug"),
        )
        for p in projects
    ]
    assignment_models = [ThreadProjectAssignment(**a) for a in assignments]

    result = ClusteringResult(
        projects=project_models,
        assignments=assignment_models,
        run_meta=_run_meta(
            params, modularity, len(set(membership)), 0.0, strategy
        ),
    )
    if debug:
        result.run_meta["_debug_feats"] = True
    return result


def _run_meta(params, modularity, n_communities, orphan_ratio, strategy):
    return dict(
        seed=params.seed,
        params_hash=params.hash(),
        modularity=round(float(modularity), 4),
        n_communities=int(n_communities),
        orphan_ratio=round(float(orphan_ratio), 4),
        strategy=strategy,
    )


# ── convenience entry: build features then cluster ───────────────────────────

def cluster_from_store(
    threads,
    messages_by_thread: dict,
    email_to_person_id: dict,
    owner_person_id,
    *,
    embed_fn,
    nlp,
    prev_result: ClusteringResult | None = None,
    overrides: dict | None = None,
    params: ClusteringParams = PARAMS,
    debug: bool = False,
) -> ClusteringResult:
    tfidf = ThreadTfidf.fit(threads, messages_by_thread)
    feats = build_thread_features(
        threads, messages_by_thread, email_to_person_id, owner_person_id,
        embed_fn=embed_fn, nlp=nlp, tfidf=tfidf,
    )
    return cluster_mailbox(
        feats, prev_result=prev_result, overrides=overrides, params=params, debug=debug
    )


# ── debug artifacts (§21) ────────────────────────────────────────────────────

def write_debug_artifacts(feats, result: ClusteringResult, pidf, out_dir, params=PARAMS):
    """Emit cluster_cards.json and similarity_graph.graphml into ``out_dir``."""
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    cards = []
    for p in result.projects:
        cards.append(
            dict(
                id=p.id,
                label=p.label,
                label_source=p.label_source.value,
                n_threads=len(p.thread_ids),
                top_members=[m.person_id for m in p.members[:5]],
                confidence=p.confidence,
                debug=p.debug,
            )
        )
    (out / "cluster_cards.json").write_text(
        json.dumps(cards, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    # GraphML: nodes=threads, edges=sim over candidate pairs.
    _idf, ub, df = participant_idf(feats, params.drop_frac)
    pairs = candidate_pairs(feats, ub, df, params)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <key id="w" for="edge" attr.name="weight" attr.type="double"/>',
        '  <graph edgedefault="undirected">',
    ]
    for i, f in enumerate(feats):
        lines.append(f'    <node id="n{i}"/>')
    for i, j in pairs:
        s = thread_similarity(feats[i], feats[j], pidf, params)
        if s >= params.tau:
            lines.append(
                f'    <edge source="n{i}" target="n{j}"><data key="w">{s:.6f}</data></edge>'
            )
    lines += ["  </graph>", "</graphml>"]
    (out / "similarity_graph.graphml").write_text("\n".join(lines), encoding="utf-8")


# ── CLI ──────────────────────────────────────────────────────────────────────

def _main(argv=None):
    ap = argparse.ArgumentParser(description="Cluster a mailbox into projects (spec 03).")
    ap.add_argument("--mailbox", required=True, help="Path to a mailbox fixture JSON.")
    ap.add_argument("--out", required=True, help="Output ClusteringResult JSON path.")
    ap.add_argument("--prev", default=None, help="Previous ClusteringResult JSON (carry-over).")
    ap.add_argument("--owner-email", default="alex@acme.com")
    ap.add_argument("--internal-domain", action="append", default=["acme.com"])
    ap.add_argument("--debug", action="store_true", help="Emit debug artifacts.")
    ap.add_argument("--debug-dir", default="cluster_debug")
    args = ap.parse_args(argv)

    from services.enrich.clustering.testkit import FakeNlp, make_test_embed
    from services.enrich.pipeline import run_enrichment
    from services.ingest.params import IngestParams
    from services.ingest.pipeline import IngestConfig, run_ingest

    cfg = IngestConfig(
        provider="fixture", mailbox_path=args.mailbox,
        owner_email=args.owner_email, internal_domains=args.internal_domain,
        params=IngestParams(),
    )
    store = run_ingest(cfg)
    enrich = run_enrichment(store.messages, args.owner_email, args.internal_domain)
    e2p = {i.email: i.person_id for i in enrich.identities if i.person_id}
    by_thread: dict = {}
    for m in store.messages:
        by_thread.setdefault(m.thread_id, []).append(m)

    prev = None
    if args.prev:
        prev = ClusteringResult.model_validate_json(open(args.prev, encoding="utf-8").read())

    result = cluster_from_store(
        store.threads, by_thread, e2p, e2p[args.owner_email],
        embed_fn=make_test_embed(), nlp=FakeNlp(), prev_result=prev, debug=args.debug,
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(result.model_dump_json(indent=2))
    print(f"[ok] wrote {args.out}: {len(result.projects)} projects")

    if args.debug:
        tfidf = ThreadTfidf.fit(store.threads, by_thread)
        feats = build_thread_features(
            store.threads, by_thread, e2p, e2p[args.owner_email],
            embed_fn=make_test_embed(), nlp=FakeNlp(), tfidf=tfidf,
        )
        pidf, _, _ = participant_idf(feats, PARAMS.drop_frac)
        write_debug_artifacts(feats, result, pidf, args.debug_dir)
        print(f"[ok] debug artifacts in {args.debug_dir}/")


if __name__ == "__main__":
    _main()
