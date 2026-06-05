"""L1 enrichment orchestrator (spec 01 §8): identity → graph → roles → clustering."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from ekc_schemas import ClusteringResult, Edge, Event, Identity, Org, Person

# Clustering deps are optional (pip install .[clustering]).  Do NOT import them at
# module level — that would break pip install -e .[test] without the clustering
# extra.  All clustering imports live inside _run_clustering() which is only called
# when ``threads`` is passed to run_enrichment().
if TYPE_CHECKING:  # IDE / mypy only — never executed at runtime
    from .clustering.params import ClusteringParams
    from .events import EventParams, ExtractFn

from .graph import build_relationship_graph
from .identity import resolve_identities
from .params import EnrichParams
from .roles import infer_roles


@dataclass
class EnrichResult:
    people: list[Person]
    identities: list[Identity]
    orgs: list[Org]
    edges: list[Edge]
    clustering: ClusteringResult | None = None  # None if < 2 clusterable threads
    events: list[Event] = field(default_factory=list)  # [] unless extract_fn supplied


def run_enrichment(
    messages,
    owner_email: str,
    internal_domains: list[str],
    params: EnrichParams | None = None,
    now: datetime | None = None,
    *,
    threads=None,
    embed_fn=None,
    nlp=None,
    cluster_params: ClusteringParams | None = None,  # default resolved lazily inside _run_clustering
    prev_clustering: ClusteringResult | None = None,
    extract_fn: ExtractFn | None = None,
    event_params: EventParams | None = None,
) -> EnrichResult:
    """Run L1 enrichment.

    Clustering runs only when ``threads`` is supplied (the L0 thread records).
    ``embed_fn`` and ``nlp`` are injectable; when omitted, the production loaders
    are used (real embedding model + spaCy ``en_core_web_sm``, which raises a
    clear error if the model is missing). Tests pass deterministic fakes.
    """
    if params is None:
        params = EnrichParams()
    people, identities, orgs = resolve_identities(
        messages, owner_email, internal_domains, params
    )
    email_to_pid = {i.email: i.person_id for i in identities if i.person_id is not None}
    edges = build_relationship_graph(messages, owner_email, email_to_pid, params, now=now)
    people = infer_roles(people, messages, email_to_pid, internal_domains, params)

    clustering = None
    if threads is not None:
        clustering = _run_clustering(
            threads, messages, email_to_pid, owner_email,
            embed_fn=embed_fn, nlp=nlp, params=cluster_params,
            prev_clustering=prev_clustering,
        )

    # Event extraction (S4 / D10) runs only when threads are supplied AND an
    # extract_fn is injected. extract_fn is the seam keeping the network call
    # out of the deterministic path; without it, events stays [].
    events: list[Event] = []
    if threads is not None and extract_fn is not None:
        from .events import extract_events  # noqa: PLC0415

        owner_pid = email_to_pid.get(owner_email)
        if owner_pid is not None:
            by_thread: dict = {}
            for m in messages:
                by_thread.setdefault(m.thread_id, []).append(m)
            assignments = (
                list(clustering.assignments) if clustering is not None else []
            )
            events = extract_events(
                threads,
                by_thread,
                assignments,
                email_to_pid,
                owner_pid,
                extract_fn=extract_fn,
                params=event_params,
            )

    return EnrichResult(
        people=people, identities=identities, orgs=orgs, edges=edges,
        clustering=clustering, events=events,
    )


def _run_clustering(
    threads, messages, email_to_pid, owner_email, *,
    embed_fn, nlp, params, prev_clustering,
):
    """Build per-thread message map and cluster. Returns None if < 2 threads.

    All clustering imports are lazy so the module loads without the clustering extra.
    """
    # Lazy imports — only resolved when clustering is actually requested.
    from .clustering.params import PARAMS as CLUSTER_PARAMS  # noqa: PLC0415
    from .clustering.pipeline import cluster_from_store  # noqa: PLC0415

    if params is None:
        params = CLUSTER_PARAMS

    by_thread: dict = {}
    for m in messages:
        by_thread.setdefault(m.thread_id, []).append(m)
    clusterable = [t for t in threads if by_thread.get(t.id)]
    if len(clusterable) < 2:
        return None

    owner_pid = email_to_pid.get(owner_email)
    if owner_pid is None:
        return None

    if embed_fn is None:
        from .clustering.embed import load_embed_fn

        embed_fn = load_embed_fn()
    if nlp is None:
        from .clustering.nlp import load_nlp

        nlp = load_nlp()

    return cluster_from_store(
        clusterable, by_thread, email_to_pid, owner_pid,
        embed_fn=embed_fn, nlp=nlp, params=params, prev_result=prev_clustering,
    )
