"""S13 relationship-map derivation tests.

Builds a small, fully controlled mailbox directly via ORM inserts so each
relationship type and exclusion rule can be asserted precisely and
deterministically. DB-gated like the other integration suites.

Scenario (owner = alex@acme.com):
  People:  Alex(owner), Bob, Carol (acme.com), Dave (vendor.com),
           Grace, Heidi (acme, sensitive-thread-only), Ivan, Judy (acme, noise-only)
  Orgs:    Acme (acme.com, internal)
  Projects: P1 "Nexus Auth" (Bob, Carol, Dave), P2 "Connection Pool" (Bob, Dave)
            → Bob and Dave span 2 projects (bridge); Carol spans 1.
  Threads:  T1 [alex,bob,carol] P1 eligible; T2 [bob,carol,dave] P1 eligible;
            T3 [grace,heidi] P1 with an HR message (excluded);
            T4 [ivan,judy] P1 all-noise (excluded).
  Edges:    owner→Bob/Carol/Dave have safe direct evidence; owner→Grace is
            an unsafe aggregate and must not surface.
"""

# Note: direct Exchange rows are aggregate L1 data, but S13 may only surface a
# direct relationship when there is safe message-header evidence in eligible
# threads. Grace intentionally has an aggregate Edge row below and no safe direct
# citation; she must not appear through that aggregate.
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")


def _db_reachable() -> bool:
    if not DATABASE_URL:
        return False
    try:
        from services.db.engine import engine
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL not set or Postgres unreachable."
)

OWNER = "alex@acme.com"
_T = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


def _build_scenario(session):
    """Insert the controlled scenario; return (mailbox_id, ids dict)."""
    from services.db import models as orm

    mbx = orm.Mailbox(
        provider="gmail", owner_email=OWNER, status="active",
        embed_model="voyage-4", embed_dim=1024, config={},
    )
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)

    acme = orm.Org(mailbox_id=mid, name="Acme", domains=["acme.com"], internal=True)
    session.add(acme)
    session.commit()

    people = {
        "alex": ("alex@acme.com", str(acme.id), "internal"),
        "bob": ("bob@acme.com", str(acme.id), "internal"),
        "carol": ("carol@acme.com", str(acme.id), "internal"),
        "dave": ("dave@vendor.com", None, "vendor"),
        "grace": ("grace@acme.com", str(acme.id), "internal"),
        "heidi": ("heidi@acme.com", str(acme.id), "internal"),
        "ivan": ("ivan@acme.com", str(acme.id), "internal"),
        "judy": ("judy@acme.com", str(acme.id), "internal"),
    }
    pid: dict[str, str] = {}
    for key, (email, org_id, role) in people.items():
        person = orm.Person(
            mailbox_id=mid, canonical_email=email, names=[key.capitalize()],
            org_id=org_id, role=role, role_confidence=0.9,
        )
        session.add(person)
        session.commit()
        pid[key] = str(person.id)
        session.add(orm.Identity(
            mailbox_id=mid, email=email, display_names=[key.capitalize()],
            person_id=person.id,
        ))
    session.commit()

    # Projects + members.
    proj: dict[str, str] = {}
    for label, members in (("Nexus Auth", ["bob", "carol", "dave"]),
                           ("Connection Pool", ["bob", "dave"])):
        pr = orm.Project(
            mailbox_id=mid, label=label, label_source="ctfidf",
            start=_T, end=_T, confidence=0.7,
        )
        session.add(pr)
        session.commit()
        proj[label] = str(pr.id)
        for m in members:
            session.add(orm.ProjectMember(
                project_id=pr.id, person_id=pid[m], involvement=1.0, message_count=3,
            ))
    session.commit()

    def _thread(parts, label):
        t = orm.Thread(
            mailbox_id=mid, provider_thread_ids=[], subject_norm=label,
            participants=[people[p][0] for p in parts], t_start=_T, t_end=_T,
        )
        session.add(t)
        session.commit()
        return t

    def _msg(thread, sender, tos, sens, noise, hdr):
        session.add(orm.Message(
            mailbox_id=mid, message_id_header=hdr, provider_id=hdr,
            thread_id=thread.id, sender_email=people[sender][0],
            to_emails=[people[t][0] for t in tos], cc_emails=[], addresses={},
            ts=_T, subject="s", clean_text="c", link_domains=[],
            sensitivity=sens, noise=noise,
        ))

    t1 = _thread(["alex", "bob", "carol"], "Nexus Auth")
    _msg(t1, "alex", ["bob", "carol"], ["none"], False, "<t1m1@acme>")
    t2 = _thread(["bob", "carol", "dave"], "Nexus Auth")
    _msg(t2, "bob", ["carol", "dave"], ["none"], False, "<t2m1@acme>")
    t2b = _thread(["alex", "dave"], "Connection Pool")
    _msg(t2b, "alex", ["dave"], ["none"], False, "<t2bm1@acme>")
    t3 = _thread(["grace", "heidi"], "Nexus Auth")  # sensitive (excluded)
    _msg(t3, "grace", ["heidi"], ["none"], False, "<t3m1@acme>")
    _msg(t3, "heidi", ["grace"], ["hr"], False, "<t3m2@acme>")
    t4 = _thread(["ivan", "judy"], "Nexus Auth")  # all-noise (excluded)
    _msg(t4, "ivan", ["judy"], ["none"], True, "<t4m1@acme>")
    session.commit()

    p1 = proj["Nexus Auth"]
    p2 = proj["Connection Pool"]
    for t in (t1, t2, t3, t4):
        session.add(orm.ThreadProjectAssignment(
            thread_id=t.id, project_id=p1, weight=1.0, is_primary=True,
        ))
    session.add(orm.ThreadProjectAssignment(
        thread_id=t2b.id, project_id=p2, weight=1.0, is_primary=True,
    ))
    session.commit()

    for who, w in (("bob", 0.8), ("carol", 0.5), ("dave", 0.1), ("grace", 0.9)):
        session.add(orm.Edge(
            mailbox_id=mid, person_id=pid[who], message_count=5, sent_to_count=3,
            received_count=2, first_contact=_T, last_contact=_T, weight=w,
        ))
    session.commit()

    return mid, {"pid": pid, "proj": proj}


@pytest.fixture()
def scenario():
    from services.db import models as orm
    from services.db.engine import SessionLocal

    session = SessionLocal()
    mid, ids = _build_scenario(session)
    try:
        yield session, mid, ids
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        session.commit()
        session.close()


def _derive(session, mid, **kw):
    from services.db import models as orm
    from services.relationships.derive import derive_relationship_map
    mbx = session.get(orm.Mailbox, mid)
    return derive_relationship_map(session, mid, mbx, **kw)


def _edges_of(resp, rtype):
    return [e for e in resp.edges if e.relationship_type == rtype]


# ── Owner mode ────────────────────────────────────────────────────────────────

@requires_db
def test_owner_mode_direct_exchange(scenario):
    session, mid, ids = scenario
    resp = _derive(session, mid, mode="owner")
    assert resp.root is not None and resp.root.node_type == "owner"
    direct = _edges_of(resp, "direct_exchange")
    targets = {e.target_id for e in direct}
    assert targets == {ids["pid"]["bob"], ids["pid"]["carol"], ids["pid"]["dave"]}
    # Direct edges are backed only by safe message-header evidence; the unsafe
    # aggregate Grace edge must not surface.
    assert ids["pid"]["grace"] not in targets
    dave = next(e for e in direct if e.target_id == ids["pid"]["dave"])
    bob = next(e for e in direct if e.target_id == ids["pid"]["bob"])
    assert dave.source_message_ids == ["<t2bm1@acme>"]
    assert dave.evidence_count == 1
    assert bob.evidence_count == 1
    # Owner direct edge carries message-header evidence for Bob (T1 message).
    assert "<t1m1@acme>" in bob.source_message_ids


@requires_db
def test_owner_mode_contact_crosslinks(scenario):
    session, mid, ids = scenario
    resp = _derive(session, mid, mode="owner")
    # Bob & Carol are co-present (T1/T2) and co-members (P1) → cross-link present.
    pair = {ids["pid"]["bob"], ids["pid"]["carol"]}
    copres = [
        e for e in resp.edges
        if e.relationship_type in ("thread_copresence", "project_copresence")
        and {e.source_id, e.target_id} == pair
    ]
    assert copres, "expected Bob–Carol co-presence cross-link in owner mode"


# ── Project mode ──────────────────────────────────────────────────────────────

@requires_db
def test_project_mode_membership(scenario):
    session, mid, ids = scenario
    resp = _derive(session, mid, mode="project", project_id=ids["proj"]["Nexus Auth"])
    assert resp.root is not None and resp.root.node_type == "project"
    node_ids = {n.id for n in resp.nodes}
    for m in ("bob", "carol", "dave"):
        assert ids["pid"][m] in node_ids
    # project → member structural edges exist.
    proj_edges = [
        e for e in _edges_of(resp, "project_copresence")
        if e.source_id == ids["proj"]["Nexus Auth"]
    ]
    assert {e.target_id for e in proj_edges} >= {
        ids["pid"]["bob"], ids["pid"]["carol"], ids["pid"]["dave"]
    }


# ── Org mode ──────────────────────────────────────────────────────────────────

@requires_db
def test_org_mode_grouping(scenario):
    session, mid, ids = scenario
    resp = _derive(session, mid, mode="org")
    org_nodes = [n for n in resp.nodes if n.node_type == "organization"]
    domains = {n.subtitle for n in org_nodes}
    assert "acme.com" in domains and "vendor.com" in domains
    assert _edges_of(resp, "org_affiliation"), "expected org_affiliation edges"
    assert resp.groups, "expected org groups"
    # Acme org should be named from the Org row, vendor from the bare domain.
    acme = next(n for n in org_nodes if n.subtitle == "acme.com")
    assert acme.label == "Acme"


# ── Graph mode + bridge ───────────────────────────────────────────────────────

@requires_db
def test_graph_mode_has_person_person_edges(scenario):
    session, mid, ids = scenario
    resp = _derive(session, mid, mode="graph")
    assert resp.layout_hint == "graph"
    assert _edges_of(resp, "thread_copresence") or _edges_of(resp, "project_copresence")


@requires_db
def test_bridge_contact(scenario):
    session, mid, ids = scenario
    resp = _derive(session, mid, mode="graph")
    nodes = {n.id: n for n in resp.nodes}
    # Bob and Dave span 2 projects → bridge; Carol spans 1 → not.
    assert nodes[ids["pid"]["bob"]].metadata.get("is_bridge") is True
    assert nodes[ids["pid"]["dave"]].metadata.get("is_bridge") is True
    assert nodes[ids["pid"]["carol"]].metadata.get("is_bridge") is False
    bridge_edges = _edges_of(resp, "bridge")
    assert any(e.source_id == ids["pid"]["bob"] for e in bridge_edges)


# ── Exclusion rules ───────────────────────────────────────────────────────────

@requires_db
def test_sensitive_thread_excluded(scenario):
    session, mid, ids = scenario
    resp = _derive(session, mid, mode="graph")
    node_ids = {n.id for n in resp.nodes}
    # Grace/Heidi appear only in a sensitive thread → never surfaced.
    assert ids["pid"]["grace"] not in node_ids
    assert ids["pid"]["heidi"] not in node_ids
    pair = {ids["pid"]["grace"], ids["pid"]["heidi"]}
    assert not any({e.source_id, e.target_id} == pair for e in resp.edges)
    # No citation ever references the sensitive thread's messages.
    for e in resp.edges:
        assert "<t3m2@acme>" not in e.source_message_ids
        assert "<t3m1@acme>" not in e.source_message_ids


@requires_db
def test_noise_thread_excluded(scenario):
    session, mid, ids = scenario
    resp = _derive(session, mid, mode="graph")
    node_ids = {n.id for n in resp.nodes}
    assert ids["pid"]["ivan"] not in node_ids
    assert ids["pid"]["judy"] not in node_ids


# ── Determinism + empty ───────────────────────────────────────────────────────

@requires_db
def test_deterministic_output(scenario):
    session, mid, ids = scenario
    a = _derive(session, mid, mode="graph").model_dump(mode="json")
    b = _derive(session, mid, mode="graph").model_dump(mode="json")
    assert a == b


@requires_db
def test_relationship_type_filter(scenario):
    session, mid, ids = scenario
    resp = _derive(session, mid, mode="graph", relationship_types=["direct_exchange"])
    assert all(e.relationship_type == "direct_exchange" for e in resp.edges)


@requires_db
def test_empty_mailbox_clean_response(scenario):
    from services.db import models as orm
    from services.db.engine import SessionLocal
    session, _mid, _ids = scenario

    s2 = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email="nobody@nowhere.com", status="active",
        embed_model="voyage-4", embed_dim=1024, config={},
    )
    s2.add(mbx)
    s2.commit()
    empty_id = str(mbx.id)
    try:
        resp = _derive(s2, empty_id, mode="owner")
        # Synthetic owner root, no contacts, no edges — valid, no crash.
        assert resp.root is not None and resp.root.node_type == "owner"
        assert resp.edges == []
        assert resp.generated_from.eligible_threads == 0
    finally:
        s2.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == empty_id))
        s2.commit()
        s2.close()


# ── API endpoint ──────────────────────────────────────────────────────────────

@requires_db
def test_api_relationship_map_bad_mailbox_404():
    from fastapi.testclient import TestClient
    from services.api.main import app
    client = TestClient(app)
    r = client.get(f"/api/relationship-map/{uuid.uuid4()}")
    assert r.status_code == 404


@requires_db
def test_api_relationship_map_malformed_mailbox_404():
    from fastapi.testclient import TestClient
    from services.api.main import app
    client = TestClient(app)
    r = client.get("/api/relationship-map/not-a-uuid")
    assert r.status_code == 404


@requires_db
def test_api_relationship_map_invalid_relationship_type_422(scenario):
    from fastapi.testclient import TestClient
    from services.api.main import app
    _session, mid, _ids = scenario
    client = TestClient(app)
    r = client.get(f"/api/relationship-map/{mid}?relationship_types=typo")
    assert r.status_code == 422


@requires_db
def test_api_relationship_map_ok(scenario):
    from fastapi.testclient import TestClient
    from services.api.main import app
    _session, mid, _ids = scenario
    client = TestClient(app)
    r = client.get(f"/api/relationship-map/{mid}?mode=owner")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "owner"
    assert body["root"]["node_type"] == "owner"
    assert body["layout_hint"] == "tree"
