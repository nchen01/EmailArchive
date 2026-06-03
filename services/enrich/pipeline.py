"""L1 enrichment orchestrator (spec 01 §8): identity → graph → roles."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ekc_schemas import Edge, Identity, Org, Person

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


def run_enrichment(
    messages,
    owner_email: str,
    internal_domains: list[str],
    params: EnrichParams | None = None,
    now: datetime | None = None,
) -> EnrichResult:
    if params is None:
        params = EnrichParams()
    people, identities, orgs = resolve_identities(
        messages, owner_email, internal_domains, params
    )
    email_to_pid = {i.email: i.person_id for i in identities if i.person_id is not None}
    edges  = build_relationship_graph(messages, owner_email, email_to_pid, params, now=now)
    people = infer_roles(people, messages, email_to_pid, internal_domains, params)
    return EnrichResult(people=people, identities=identities, orgs=orgs, edges=edges)
