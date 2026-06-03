"""L1 §3 — identity resolution. Collapse addresses/aliases into one Person.

Strong precision bias: a false person-merge corrupts the whole graph. We only
merge two addresses when they share an org domain AND their display names are
fuzzy-equal above the threshold. Distinct first-name collisions across domains
are never compared (blocking is by domain).
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from rapidfuzz import fuzz

from ekc_schemas import Address, Identity, Org, Person, Role

from .params import EnrichParams

_NS = uuid.NAMESPACE_DNS


class _UnionFind:
    def __init__(self):
        self.p: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.p.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.p[self.find(a)] = self.find(b)


def _domain_of(email: str) -> str:
    return email.split("@", 1)[1] if "@" in email else ""


def _name_similarity(names_a: list[str], names_b: list[str]) -> float:
    if not names_a or not names_b:
        return 0.0
    best = 0.0
    for n1 in names_a:
        for n2 in names_b:
            best = max(best, fuzz.ratio(n1.lower(), n2.lower()))
    return best


def _collect_addresses(messages: list) -> dict[str, dict]:
    """email -> {names: ordered-unique list}."""
    addr: dict[str, dict] = defaultdict(lambda: {"names": []})

    def add(a: Address) -> None:
        rec = addr[a.email]
        for n in a.display_names:
            if n and n not in rec["names"]:
                rec["names"].append(n)
        # ensure key exists even with no name
        addr[a.email] = rec

    for m in messages:
        add(m.sender)
        for a in m.to:
            add(a)
        for a in m.cc:
            add(a)
    return dict(addr)


def resolve_identities(
    messages: list,
    owner_email: str,
    internal_domains: list[str],
    params: EnrichParams,
) -> tuple[list[Person], list[Identity], list[Org]]:
    addr_map = _collect_addresses(messages)

    uf = _UnionFind()
    by_domain: dict[str, list[str]] = defaultdict(list)
    for email in addr_map:
        uf.add(email)
        by_domain[_domain_of(email)].append(email)

    # Fuzzy display-name merge, blocked by domain.
    for emails in by_domain.values():
        emails_sorted = sorted(emails)
        for i, a in enumerate(emails_sorted):
            for b in emails_sorted[i + 1:]:
                sim = _name_similarity(addr_map[a]["names"], addr_map[b]["names"])
                if sim >= params.name_similarity_threshold * 100:
                    uf.union(a, b)

    # Group emails by component.
    components: dict[str, list[str]] = defaultdict(list)
    for email in addr_map:
        components[uf.find(email)].append(email)

    internal_set = {d.lower() for d in internal_domains}

    # Orgs — one per domain.
    orgs_by_domain: dict[str, Org] = {}
    for email in addr_map:
        domain = _domain_of(email)
        if domain and domain not in orgs_by_domain:
            orgs_by_domain[domain] = Org(
                id=str(uuid.uuid5(_NS, "org:" + domain)),
                name=domain,
                domains=[domain],
                internal=domain in internal_set,
            )

    people: list[Person] = []
    identities: list[Identity] = []

    for component_emails in components.values():
        emails_sorted = sorted(component_emails)
        # Canonical email = UF root (deterministic).
        canonical = uf.find(emails_sorted[0])
        names: list[str] = []
        for e in emails_sorted:
            for n in addr_map[e]["names"]:
                if n not in names:
                    names.append(n)
        person_id = str(uuid.uuid5(_NS, "person:" + canonical))
        domain = _domain_of(canonical)
        org_id = orgs_by_domain[domain].id if domain in orgs_by_domain else None

        person = Person(
            id=person_id,
            canonical_email=canonical,
            names=sorted(names),
            org_id=org_id,
            role=Role.UNKNOWN,
            role_confidence=0.0,
            identities=emails_sorted,
        )
        people.append(person)
        for e in emails_sorted:
            identities.append(
                Identity(
                    email=e,
                    display_names=list(addr_map[e]["names"]),
                    person_id=person_id,
                )
            )

    people.sort(key=lambda p: p.canonical_email)
    identities.sort(key=lambda i: i.email)
    orgs = sorted(orgs_by_domain.values(), key=lambda o: o.domains[0])
    return people, identities, orgs
