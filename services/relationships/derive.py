"""Live relationship-map derivation from existing L1 tables (S13.2).

Everything is computed per request from Person / Identity / Thread / Message /
Edge / Org / Project / ProjectMember / ThreadProjectAssignment. Nothing is
persisted.

PERSISTENCE NOTE (Q5): this is API-derived for now. The per-request cost is
dominated by (a) loading the mailbox's safe messages and (b) the O(participants^2)
pairing for thread/project co-presence. That is fine at demo/smoke scale (tens to
low-hundreds of threads). If a mailbox grows large enough that this endpoint
becomes slow, or we need auditing/repeatability guarantees, materialize a
``relationship_edge`` table (mailbox_id, source/target, type, evidence ids,
weight, first/last_seen) refreshed alongside L1 and read it here instead. The
contracts in this package are already shaped so that swap is transparent to the
API/UI.

Determinism: all ids are uuid5 over sorted inputs; all collections are sorted
before returning; "now" for staleness/recency is the latest thread timestamp in
the mailbox, never wall-clock.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from services.db import models as orm

from .contracts import (
    GeneratedFrom,
    RelationshipEdge,
    RelationshipGroup,
    RelationshipMapResponse,
    RelationshipNode,
)
from .params import DEFAULT_PARAMS, RelationshipParams

# Fixed namespace so relationship ids are stable across runs/processes.
_NS = uuid.UUID("6f1d2c3a-9b4e-5a6f-8c7d-0e1f2a3b4c5d")


def _rid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


# ── Internal accumulator for a person<->person / person<->X relationship ──────

@dataclass
class _Rel:
    rel_type: str
    a: str
    b: str
    thread_ids: set[str] = field(default_factory=set)
    project_ids: set[str] = field(default_factory=set)
    message_ids: set[str] = field(default_factory=set)
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def touch(self, start: datetime | None, end: datetime | None) -> None:
        start, end = _aware(start), _aware(end)
        if start is not None:
            self.first_seen = start if self.first_seen is None else min(self.first_seen, start)
        if end is not None:
            self.last_seen = end if self.last_seen is None else max(self.last_seen, end)


@dataclass
class _Base:
    owner_email: str
    owner_node_id: str
    owner_person_id: str | None
    owner_label: str
    persons: dict           # person_id -> orm.Person
    person_emails: dict     # person_id -> set[email]
    email_to_pid: dict      # email -> person_id
    orgs: dict              # org_id -> orm.Org
    projects: dict          # project_id -> orm.Project
    members_by_project: dict  # project_id -> sorted list[person_id] (non-owner)
    projects_by_person: dict  # person_id -> set[project_id]
    member_involvement: dict  # (project_id, person_id) -> involvement
    edges: list             # list[orm.Edge]
    thread_info: dict       # thread_id -> (t_start, t_end, set[project_id], list[node_id participants])
    owner_dm_headers: dict  # email -> sorted list[message_id_header]
    owner_dm_seen: dict     # email -> (first_seen, last_seen) from safe messages
    relevant_pids: set      # persons that appear in an eligible relationship (never sensitive-only)
    now: datetime | None
    counts: GeneratedFrom


# ── Eligibility (whole-thread sensitivity exclusion, like S9) ─────────────────

def _eligible_thread_ids(session: Session, mailbox_id: str) -> tuple[set[str], int, int]:
    """Threads usable for relationship inference: >=1 non-noise '{none}' message
    AND no message with sensitivity != '{none}'. Returns (eligible, total, excluded).
    """
    total = session.execute(
        select(func.count()).select_from(orm.Thread).where(orm.Thread.mailbox_id == mailbox_id)
    ).scalar() or 0

    rows = session.execute(
        text(
            "SELECT t.id::text FROM thread t "
            "WHERE t.mailbox_id = :mid "
            "  AND EXISTS (SELECT 1 FROM message m WHERE m.thread_id = t.id "
            "      AND m.mailbox_id = :mid AND m.noise = false AND m.sensitivity = '{none}') "
            "  AND NOT EXISTS (SELECT 1 FROM message m2 WHERE m2.thread_id = t.id "
            "      AND m2.mailbox_id = :mid AND m2.sensitivity != '{none}')"
        ),
        {"mid": mailbox_id},
    ).all()
    eligible = {r[0] for r in rows}
    return eligible, total, total - len(eligible)


def _load_base(session: Session, mailbox_id: str, mbx: orm.Mailbox,
               params: RelationshipParams) -> _Base:
    # Persons + identities.
    persons = {
        str(p.id): p
        for p in session.execute(
            select(orm.Person).where(orm.Person.mailbox_id == mailbox_id)
        ).scalars()
    }
    person_emails: dict[str, set[str]] = defaultdict(set)
    email_to_pid: dict[str, str] = {}
    for email, pid in session.execute(
        select(orm.Identity.email, orm.Identity.person_id).where(
            orm.Identity.mailbox_id == mailbox_id, orm.Identity.person_id.isnot(None)
        )
    ):
        spid = str(pid)
        email_to_pid[email] = spid
        person_emails[spid].add(email)

    owner_email = mbx.owner_email
    owner_person_id = email_to_pid.get(owner_email)
    owner_node_id = owner_person_id or f"owner-{mailbox_id}"
    owner_label = (
        (persons[owner_person_id].names or [owner_email])[0]
        if owner_person_id and owner_person_id in persons
        else owner_email
    )

    orgs = {
        str(o.id): o
        for o in session.execute(
            select(orm.Org).where(orm.Org.mailbox_id == mailbox_id)
        ).scalars()
    }
    projects = {
        str(pr.id): pr
        for pr in session.execute(
            select(orm.Project).where(orm.Project.mailbox_id == mailbox_id)
        ).scalars()
    }

    # Project membership (exclude owner).
    members_by_project: dict[str, list[str]] = defaultdict(list)
    projects_by_person: dict[str, set[str]] = defaultdict(set)
    member_involvement: dict[tuple[str, str], float] = {}
    if projects:
        for m in session.execute(
            select(orm.ProjectMember).where(
                orm.ProjectMember.project_id.in_(list(projects.keys()))
            )
        ).scalars():
            pid = str(m.project_id)
            per = str(m.person_id)
            if per == owner_person_id:
                continue
            members_by_project[pid].append(per)
            projects_by_person[per].add(pid)
            member_involvement[(pid, per)] = float(m.involvement)
    for pid in list(members_by_project.keys()):
        members_by_project[pid] = sorted(set(members_by_project[pid]))

    edges = list(
        session.execute(
            select(orm.Edge).where(orm.Edge.mailbox_id == mailbox_id)
        ).scalars()
    )

    # Eligible, project-relevant threads.
    eligible, total_threads, excluded = _eligible_thread_ids(session, mailbox_id)

    assigns: dict[str, set[str]] = defaultdict(set)
    for tid, pid in session.execute(
        select(orm.ThreadProjectAssignment.thread_id, orm.ThreadProjectAssignment.project_id)
    ):
        assigns[str(tid)].add(str(pid))

    # thread_info only for eligible threads that are also project-relevant.
    thread_info: dict[str, tuple] = {}
    if eligible:
        for t in session.execute(
            select(orm.Thread).where(
                orm.Thread.mailbox_id == mailbox_id,
                orm.Thread.id.in_(sorted(eligible)),
            )
        ).scalars():
            tid = str(t.id)
            tproj = assigns.get(tid, set())
            if not tproj:
                continue  # project-relevant scope (Q4)
            node_ids: list[str] = []
            for email in t.participants:
                pid = email_to_pid.get(email)
                if pid is None or pid == owner_person_id:
                    continue  # owner handled via direct_exchange; unknown emails skipped
                node_ids.append(pid)
            thread_info[tid] = (t.t_start, t.t_end, tproj, sorted(set(node_ids)))

    # Safe messages (eligible threads only) → owner direct-message headers + count.
    owner_dm_headers: dict[str, list[str]] = defaultdict(list)
    owner_dm_seen: dict[str, tuple[datetime | None, datetime | None]] = {}
    msg_count = 0
    if eligible:
        for hdr, sender, tos, ccs, ts in session.execute(
            text(
                "SELECT message_id_header, sender_email, to_emails, cc_emails, ts "
                "FROM message WHERE mailbox_id = :mid AND noise = false "
                "  AND sensitivity = '{none}' AND thread_id::text = ANY(:tids)"
            ),
            {"mid": mailbox_id, "tids": sorted(eligible)},
        ):
            msg_count += 1
            seen_ts = _aware(ts)

            def _remember(email: str) -> None:
                first, last = owner_dm_seen.get(email, (None, None))
                if seen_ts is not None:
                    first = seen_ts if first is None else min(first, seen_ts)
                    last = seen_ts if last is None else max(last, seen_ts)
                owner_dm_seen[email] = (first, last)

            recips = set(tos or []) | set(ccs or [])
            if sender == owner_email:
                for r in recips:
                    owner_dm_headers[r].append(hdr)
                    _remember(r)
            elif owner_email in recips:
                owner_dm_headers[sender].append(hdr)
                _remember(sender)
    for k in owner_dm_headers:
        owner_dm_headers[k] = sorted(set(owner_dm_headers[k]))

    now = _aware(
        session.execute(
            select(func.max(orm.Thread.t_end)).where(orm.Thread.mailbox_id == mailbox_id)
        ).scalar()
    )

    # Persons that appear in at least one *eligible* relationship source: an owner
    # Edge, an eligible project-relevant thread, or a project membership. Anyone
    # known only from excluded (sensitive/noise) threads is deliberately absent,
    # so org/bridge grouping can never resurface them.
    relevant_pids: set[str] = set()
    for e in edges:
        sp = str(e.person_id)
        has_safe_owner_headers = any(
            owner_dm_headers.get(email) for email in person_emails.get(sp, set())
        )
        if sp in persons and sp != owner_person_id and has_safe_owner_headers:
            relevant_pids.add(sp)
    for _tid, (_s, _e, _proj, parts) in thread_info.items():
        relevant_pids.update(parts)
    for _pid, members in members_by_project.items():
        relevant_pids.update(members)
    relevant_pids.discard(owner_node_id)

    counts = GeneratedFrom(
        threads=total_threads,
        projects=len(projects),
        messages=msg_count,
        eligible_threads=len(eligible),
        excluded_threads=excluded,
    )

    return _Base(
        owner_email=owner_email, owner_node_id=owner_node_id,
        owner_person_id=owner_person_id, owner_label=owner_label,
        persons=persons, person_emails=person_emails, email_to_pid=email_to_pid,
        orgs=orgs, projects=projects, members_by_project=members_by_project,
        projects_by_person=projects_by_person, member_involvement=member_involvement,
        edges=edges, thread_info=thread_info, owner_dm_headers=owner_dm_headers,
        owner_dm_seen=owner_dm_seen, relevant_pids=relevant_pids, now=now,
        counts=counts,
    )


# ── Node helpers ──────────────────────────────────────────────────────────────

def _person_label(p: orm.Person) -> str:
    return (p.names or [p.canonical_email])[0]


def _person_domain(base: _Base, pid: str) -> str:
    p = base.persons.get(pid)
    if p is None:
        return ""
    if p.org_id and str(p.org_id) in base.orgs:
        doms = base.orgs[str(p.org_id)].domains
        if doms:
            return doms[0].lower()
    return _domain_of(p.canonical_email)


def _org_node_id(domain: str) -> str:
    return _rid("org", domain)


def _build_person_node(base: _Base, pid: str) -> RelationshipNode:
    p = base.persons[pid]
    nproj = len(base.projects_by_person.get(pid, set()))
    is_bridge = nproj >= DEFAULT_PARAMS.bridge_min_projects
    return RelationshipNode(
        id=pid,
        node_type="person",
        label=_person_label(p),
        subtitle=p.canonical_email,
        role=p.role,
        confidence=float(p.role_confidence) if p.role_confidence is not None else None,
        metadata={
            "org_domain": _person_domain(base, pid),
            "project_count": nproj,
            "is_bridge": is_bridge,
        },
    )


def _owner_node(base: _Base) -> RelationshipNode:
    return RelationshipNode(
        id=base.owner_node_id, node_type="owner", label=base.owner_label,
        subtitle=base.owner_email, metadata={"is_owner": True},
    )


def _project_node(base: _Base, pid: str) -> RelationshipNode:
    pr = base.projects[pid]
    return RelationshipNode(
        id=pid, node_type="project", label=pr.label,
        subtitle="Project", confidence=float(pr.confidence),
        metadata={"label_source": pr.label_source},
    )


def _org_node(base: _Base, domain: str) -> RelationshipNode:
    # Prefer a real Org name/internal flag when one matches the domain.
    name, internal = domain, None
    for o in base.orgs.values():
        if o.domains and o.domains[0].lower() == domain:
            name = o.name
            internal = bool(o.internal)
            break
    md: dict = {"domain": domain}
    if internal is not None:
        md["internal"] = internal
    return RelationshipNode(
        id=_org_node_id(domain), node_type="organization", label=name,
        subtitle=domain, metadata=md,
    )


# ── Relationship builders (full mailbox graph) ────────────────────────────────

def _direct_exchange(base: _Base) -> list[_Rel]:
    rels: list[_Rel] = []
    for e in base.edges:
        pid = str(e.person_id)
        if pid == base.owner_person_id or pid not in base.persons:
            continue
        headers: set[str] = set()
        for email in sorted(base.person_emails.get(pid, set())):
            headers.update(base.owner_dm_headers.get(email, []))
        # Privacy/evidence gate: the Edge table is an aggregate built earlier in
        # L1 and may include messages that are outside S13's whole-thread safety
        # scope. Only surface a direct relationship when we can cite at least one
        # safe, non-sensitive message header gathered from eligible threads.
        if not headers:
            continue
        rel = _Rel("direct_exchange", base.owner_node_id, pid)
        for email in sorted(base.person_emails.get(pid, set())):
            first, last = base.owner_dm_seen.get(email, (None, None))
            rel.touch(first, last)
        rel.message_ids.update(headers)
        # Carry only safe evidence volume forward. The original Edge weight/count
        # may include content excluded by S13 privacy rules, so it is not used.
        rel.project_ids = set()  # n/a
        rel._weight = float(len(headers))  # type: ignore[attr-defined]
        rel._evidence_count = len(headers)  # type: ignore[attr-defined]
        rels.append(rel)
    return rels


def _copresence(base: _Base) -> dict[tuple[str, str], _Rel]:
    """thread_copresence + project_copresence merged by (type, a, b)."""
    acc: dict[tuple[str, str], _Rel] = {}

    # thread co-presence (eligible, project-relevant threads only)
    for tid, (t_start, t_end, tproj, parts) in base.thread_info.items():
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                a, b = parts[i], parts[j]
                key = ("thread_copresence", a, b)
                rel = acc.get(key)
                if rel is None:
                    rel = acc[key] = _Rel("thread_copresence", a, b)
                rel.thread_ids.add(tid)
                rel.project_ids.update(tproj)
                rel.touch(t_start, t_end)

    # project co-membership
    for pid, members in base.members_by_project.items():
        pr = base.projects.get(pid)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = sorted((members[i], members[j]))
                key = ("project_copresence", a, b)
                rel = acc.get(key)
                if rel is None:
                    rel = acc[key] = _Rel("project_copresence", a, b)
                rel.project_ids.add(pid)
                if pr is not None:
                    rel.touch(pr.start, pr.end)
    return acc


# ── Edge materialization ──────────────────────────────────────────────────────

def _explain(rel_type: str, n: int, base: _Base, rel: "_Rel") -> str:
    if rel_type == "direct_exchange":
        return "Direct email exchange with the mailbox owner"
    if rel_type == "thread_copresence":
        return f"Appeared together in {n} project thread{'s' if n != 1 else ''}"
    if rel_type == "project_copresence":
        labels = [base.projects[p].label for p in sorted(rel.project_ids) if p in base.projects]
        if len(labels) == 1:
            return f"Both assigned to {labels[0]}"
        return f"Share {n} project{'s' if n != 1 else ''}"
    if rel_type == "org_affiliation":
        return f"Same organization: {next(iter(rel.project_ids), '') or rel.b}"
    if rel_type == "bridge":
        return f"Bridge contact across {n} projects"
    return ""


def _to_edge(base: _Base, rel: _Rel, params: RelationshipParams,
             *, weight: float | None = None, evidence_kind: str,
             explanation: str | None = None) -> RelationshipEdge:
    if rel.rel_type == "thread_copresence":
        ev = len(rel.thread_ids)
    elif rel.rel_type in ("project_copresence", "bridge"):
        ev = len(rel.project_ids)
    elif rel.rel_type == "direct_exchange":
        ev = getattr(rel, "_evidence_count", len(rel.message_ids))
    else:
        ev = 1
    w = weight if weight is not None else getattr(rel, "_weight", float(ev))
    last = rel.last_seen
    stale = base.now is not None and last is not None and last < base.now - timedelta(days=params.stale_days)
    weak = w < params.weak_weight_threshold
    eid = _rid(base.owner_email, rel.rel_type, rel.a, rel.b, evidence_kind)
    return RelationshipEdge(
        id=eid, source_id=rel.a, target_id=rel.b,
        relationship_type=rel.rel_type, evidence_kind=evidence_kind,
        weight=round(float(w), 4), evidence_count=int(ev),
        source_message_ids=sorted(rel.message_ids)[: params.max_source_message_ids],
        thread_ids=sorted(rel.thread_ids), project_ids=sorted(rel.project_ids),
        first_seen=rel.first_seen, last_seen=last,
        muted=bool(weak or stale),
        explanation=explanation or _explain(rel.rel_type, ev, base, rel),
    )


# ── Public entry point ────────────────────────────────────────────────────────

def derive_relationship_map(
    session: Session,
    mailbox_id: str,
    mbx: orm.Mailbox,
    *,
    mode: str = "owner",
    root_id: str | None = None,
    project_id: str | None = None,
    min_weight: float = 0.0,
    relationship_types: list[str] | None = None,
    recency_days: int | None = None,
    params: RelationshipParams = DEFAULT_PARAMS,
) -> RelationshipMapResponse:
    base = _load_base(session, mailbox_id, mbx, params)
    type_filter = set(relationship_types) if relationship_types else None

    nodes: dict[str, RelationshipNode] = {}
    edges: list[RelationshipEdge] = []
    groups: list[RelationshipGroup] = []

    def ensure_person(pid: str) -> bool:
        if pid == base.owner_node_id:
            nodes.setdefault(base.owner_node_id, _owner_node(base))
            return True
        if pid in base.persons:
            nodes.setdefault(pid, _build_person_node(base, pid))
            return True
        return False

    direct = _direct_exchange(base)
    copres = _copresence(base)
    layout: str = "tree"

    if mode == "graph":
        layout = "graph"
        for rel in direct:
            if type_filter and "direct_exchange" not in type_filter:
                break
            if ensure_person(rel.a) and ensure_person(rel.b):
                edges.append(_to_edge(base, rel, params, evidence_kind="message_headers"))
        for rel in copres.values():
            if type_filter and rel.rel_type not in type_filter:
                continue
            ek = "thread_ids" if rel.rel_type == "thread_copresence" else "project_ids"
            if ensure_person(rel.a) and ensure_person(rel.b):
                edges.append(_to_edge(base, rel, params, evidence_kind=ek))
        # org affiliation + bridge as graph extras (relationship-relevant people only)
        rel_pids = sorted(base.relevant_pids)
        _add_org_edges(base, nodes, edges, params, type_filter, pids=rel_pids)
        _add_bridge_edges(base, nodes, edges, params, type_filter, pids=rel_pids)

    elif mode == "org":
        _add_org_mode(base, nodes, edges, groups, params, type_filter)

    elif mode == "project":
        layout = "tree"
        pid = _resolve_project(base, root_id or project_id)
        if pid is not None:
            _add_project_mode(base, nodes, edges, params, type_filter, copres, pid)
            root = nodes.get(pid)
        else:
            root = None
        return _finalize(base, nodes, edges, groups, mode="project",
                         layout="tree", root=root, params=params,
                         min_weight=min_weight, recency_days=recency_days)

    else:  # owner (default)
        mode = "owner"
        layout = "tree"
        nodes.setdefault(base.owner_node_id, _owner_node(base))
        contact_ids: set[str] = set()
        for rel in direct:
            if type_filter and "direct_exchange" not in type_filter:
                break
            if ensure_person(rel.b):
                contact_ids.add(rel.b)
                edges.append(_to_edge(base, rel, params, evidence_kind="message_headers"))
        # cross-links among contacts only (graph truth inside the ego tree)
        for rel in copres.values():
            if type_filter and rel.rel_type not in type_filter:
                continue
            if rel.a in contact_ids and rel.b in contact_ids:
                ek = "thread_ids" if rel.rel_type == "thread_copresence" else "project_ids"
                edges.append(_to_edge(base, rel, params, evidence_kind=ek))
        root = nodes.get(base.owner_node_id)
        return _finalize(base, nodes, edges, groups, mode="owner", layout="tree",
                         root=root, params=params, min_weight=min_weight,
                         recency_days=recency_days)

    root_node = nodes.get(root_id) if root_id else None
    return _finalize(base, nodes, edges, groups, mode=mode, layout=layout,
                     root=root_node, params=params, min_weight=min_weight,
                     recency_days=recency_days)


def _resolve_project(base: _Base, pid: str | None) -> str | None:
    if pid and pid in base.projects:
        return pid
    if not base.projects:
        return None
    # Deterministic default: highest confidence, then label, then id.
    best = sorted(
        base.projects.values(),
        key=lambda pr: (-float(pr.confidence), pr.label, str(pr.id)),
    )[0]
    return str(best.id)


def _add_project_mode(base, nodes, edges, params, type_filter, copres, pid):
    nodes.setdefault(pid, _project_node(base, pid))
    members = base.members_by_project.get(pid, [])
    for per in members:
        if per not in base.persons:
            continue
        nodes.setdefault(per, _build_person_node(base, per))
        # project -> member structural edge (rendered as the tree parent link)
        rel = _Rel("project_copresence", pid, per)
        rel.project_ids.add(pid)
        pr = base.projects[pid]
        rel.touch(pr.start, pr.end)
        if not (type_filter and "project_copresence" not in type_filter):
            edges.append(_to_edge(
                base, rel, params, weight=base.member_involvement.get((pid, per), 1.0),
                evidence_kind="project_ids",
                explanation=f"Appears in project: {pr.label}",
            ))
    # person<->person co-presence among members (cross-links)
    member_set = set(members)
    for rel in copres.values():
        if rel.a in member_set and rel.b in member_set:
            if type_filter and rel.rel_type not in type_filter:
                continue
            ek = "thread_ids" if rel.rel_type == "thread_copresence" else "project_ids"
            edges.append(_to_edge(base, rel, params, evidence_kind=ek))
    # org affiliation for the members
    _add_org_edges(base, nodes, edges, params, type_filter, pids=members)


def _add_org_edges(base, nodes, edges, params, type_filter, pids):
    if type_filter and "org_affiliation" not in type_filter:
        return
    for per in pids:
        if per not in base.persons:
            continue
        domain = _person_domain(base, per)
        if not domain:
            continue
        oid = _org_node_id(domain)
        nodes.setdefault(oid, _org_node(base, domain))
        nodes.setdefault(per, _build_person_node(base, per))
        rel = _Rel("org_affiliation", per, oid)
        edges.append(_to_edge(
            base, rel, params, weight=1.0, evidence_kind="domain",
            explanation=f"Same organization: {domain}",
        ))


def _add_bridge_edges(base, nodes, edges, params, type_filter, pids):
    if type_filter and "bridge" not in type_filter:
        return
    for per in pids:
        projs = sorted(base.projects_by_person.get(per, set()))
        if len(projs) < params.bridge_min_projects:
            continue
        if per not in base.persons:
            continue
        nodes.setdefault(per, _build_person_node(base, per))
        for pj in projs:
            if pj not in base.projects:
                continue
            nodes.setdefault(pj, _project_node(base, pj))
            rel = _Rel("bridge", per, pj)
            rel.project_ids = set(projs)
            pr = base.projects[pj]
            rel.touch(pr.start, pr.end)
            edges.append(_to_edge(
                base, rel, params, weight=float(len(projs)), evidence_kind="project_ids",
                explanation=f"Bridge contact across {len(projs)} projects",
            ))


def _add_org_mode(base, nodes, edges, groups, params, type_filter):
    # Group relationship-relevant people by domain; each org/domain is a
    # first-class node (Q7). People known only from excluded threads are absent.
    by_domain: dict[str, list[str]] = defaultdict(list)
    for per in base.relevant_pids:
        if per == base.owner_person_id:
            continue
        d = _person_domain(base, per)
        if d:
            by_domain[d].append(per)
    for domain in sorted(by_domain):
        members = sorted(by_domain[domain])
        oid = _org_node_id(domain)
        nodes.setdefault(oid, _org_node(base, domain))
        member_node_ids = []
        for per in members:
            nodes.setdefault(per, _build_person_node(base, per))
            member_node_ids.append(per)
            if not (type_filter and "org_affiliation" not in type_filter):
                rel = _Rel("org_affiliation", oid, per)
                edges.append(_to_edge(
                    base, rel, params, weight=1.0, evidence_kind="domain",
                    explanation=f"Same organization: {domain}",
                ))
        groups.append(RelationshipGroup(
            id=oid, label=_org_node(base, domain).label, node_ids=member_node_ids
        ))


def _finalize(base, nodes, edges, groups, *, mode, layout, root, params,
              min_weight, recency_days) -> RelationshipMapResponse:
    # Filters: min_evidence (hide), min_weight, recency.
    recency_cutoff = None
    if recency_days is not None and base.now is not None:
        recency_cutoff = base.now - timedelta(days=recency_days)

    kept: list[RelationshipEdge] = []
    for e in edges:
        if e.evidence_count < params.min_evidence:
            continue
        if e.weight < min_weight:
            continue
        if recency_cutoff is not None and e.last_seen is not None and e.last_seen < recency_cutoff:
            continue
        kept.append(e)

    # Drop now-orphaned nodes (keep root + any node referenced by a kept edge).
    referenced = {e.source_id for e in kept} | {e.target_id for e in kept}
    if root is not None:
        referenced.add(root.id)
    nodes_out = [n for nid, n in nodes.items() if nid in referenced or (root and nid == root.id)]

    # Deterministic ordering.
    _type_rank = {"owner": 0, "project": 1, "organization": 2, "person": 3, "thread_group": 4}
    nodes_out.sort(key=lambda n: (_type_rank.get(n.node_type, 9), n.label, n.id))
    kept.sort(key=lambda e: (e.relationship_type, e.source_id, e.target_id))

    # Cap graph payloads.
    if layout == "graph":
        nodes_out = nodes_out[: params.max_graph_nodes]
        node_ids = {n.id for n in nodes_out}
        kept = [e for e in kept if e.source_id in node_ids and e.target_id in node_ids][
            : params.max_graph_edges
        ]

    return RelationshipMapResponse(
        root=root, nodes=nodes_out, edges=kept, groups=groups,
        layout_hint=layout, mode=mode, generated_from=base.counts,
    )
