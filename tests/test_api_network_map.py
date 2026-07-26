"""API tests for the network-map endpoints (spec 05 §7 acceptance gates).

Requires a reachable Postgres (DATABASE_URL); skipped otherwise. Persists the
fixture mailbox into an isolated tenant row, then drives the FastAPI app via
TestClient.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from conftest import run_full_ingest

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


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres unreachable (run `docker compose up -d`).",
)

OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]


@pytest.fixture()
def seeded():
    """Persist fixture L0+L1 into a fresh isolated mailbox; yield (client, mailbox_id)."""
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import persist_l0, persist_l1
    from services.enrich.pipeline import run_enrichment

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=OWNER_EMAIL,
        embed_model="test-none",
        embed_dim=0,
        config={"internal_domains": INTERNAL_DOMAINS},
    )
    session.add(mbx)
    session.commit()
    mailbox_id = str(mbx.id)

    store = run_full_ingest()
    persist_l0(store, mailbox_id, session)
    result = run_enrichment(
        store.messages, owner_email=OWNER_EMAIL, internal_domains=INTERNAL_DOMAINS
    )
    persist_l1(result, mailbox_id, session)

    client = TestClient(app)
    try:
        yield client, mailbox_id
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id))
        session.commit()
        session.close()


def test_full_graph(seeded):
    client, mailbox_id = seeded
    r = client.get(f"/api/network-map/{mailbox_id}")
    assert r.status_code == 200
    body = r.json()

    # 10 contact nodes (all persons minus owner).
    assert len(body["nodes"]) == 10
    assert len(body["edges"]) == 10

    # Owner present in owner block, absent from nodes/edges.
    assert body["owner"]["canonical_email"] == OWNER_EMAIL
    emails = [n["canonical_email"] for n in body["nodes"]]
    assert OWNER_EMAIL not in emails

    # Nodes + edges sorted by weight DESC.
    weights = [n["weight"] for n in body["nodes"]]
    assert weights == sorted(weights, reverse=True)
    edge_weights = [e["weight"] for e in body["edges"]]
    assert edge_weights == sorted(edge_weights, reverse=True)

    # Every edge.person_id has a node.
    node_ids = {n["person_id"] for n in body["nodes"]}
    assert {e["person_id"] for e in body["edges"]} <= node_ids

    # Identity merge: jenna@acme.com & j.park@acme.com → one node (not two).
    jenna_acme = [e for e in emails if e in ("jenna@acme.com", "j.park@acme.com")]
    assert len(jenna_acme) == 1
    # The distinct Jenna Brooks (vertexlabs) is a separate node.
    assert "jenna@vertexlabs.com" in emails


def test_roles_filter(seeded):
    client, mailbox_id = seeded
    r = client.get(f"/api/network-map/{mailbox_id}?roles=internal")
    assert r.status_code == 200
    roles = {n["role"] for n in r.json()["nodes"]}
    assert roles == {"internal"}


def test_min_weight_filter(seeded):
    client, mailbox_id = seeded
    full = client.get(f"/api/network-map/{mailbox_id}").json()
    # Pick a threshold above the smallest weight; expect fewer nodes.
    weights = sorted(n["weight"] for n in full["nodes"])
    threshold = weights[1]
    filtered = client.get(
        f"/api/network-map/{mailbox_id}?min_weight={threshold}"
    ).json()
    assert all(n["weight"] >= threshold for n in filtered["nodes"])
    assert len(filtered["nodes"]) < len(full["nodes"])


def test_contact_detail_jenna(seeded):
    client, mailbox_id = seeded
    graph = client.get(f"/api/network-map/{mailbox_id}").json()

    jenna = next(
        n
        for n in graph["nodes"]
        if n["canonical_email"] in ("jenna@acme.com", "j.park@acme.com")
    )
    r = client.get(f"/api/network-map/{mailbox_id}/contact/{jenna['person_id']}")
    assert r.status_code == 200
    detail = r.json()

    # Both merged emails appear in all_emails.
    assert set(detail["person"]["all_emails"]) == {"jenna@acme.com", "j.park@acme.com"}

    # Edge stats: received >= 3, weight > 0.
    assert detail["edge"]["received_count"] >= 3
    assert detail["edge"]["weight"] > 0

    # >= 3 recent shared threads (T1, T2, T7 all include her).
    assert len(detail["recent_threads"]) >= 3

    # other_participants excludes owner and the contact's own emails.
    for t in detail["recent_threads"]:
        assert OWNER_EMAIL not in t["other_participants"]
        assert "jenna@acme.com" not in t["other_participants"]
        assert "j.park@acme.com" not in t["other_participants"]


def test_unknown_mailbox_404(seeded):
    client, _ = seeded
    r = client.get("/api/network-map/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_owner_without_identity_graph_returns_empty_graph(seeded):
    """S17.16: a mailbox with an owner Person but no L1 identity/edge graph (e.g.
    the Handoff demo mailbox, which seeds messages/events only) returns an EMPTY
    graph (200) via the owner_person_id fallback -- not a 404 that would make the
    workspace look broken. A mailbox with NO owner at all still 404s."""
    client, _ = seeded
    from services.db import models as orm
    from services.db.engine import SessionLocal

    s = SessionLocal()
    try:
        mbx = orm.Mailbox(provider="gmail", owner_email="handoff-only@example.com",
                          embed_model="test-none", embed_dim=0, config={})
        s.add(mbx)
        s.commit()
        mid = str(mbx.id)

        # No owner_person_id yet -> still 404 (nothing to center the graph on).
        assert client.get(f"/api/network-map/{mid}").status_code == 404

        # Give it an owner Person + link, but NO Identity / Edge rows.
        owner = orm.Person(mailbox_id=mid, canonical_email="handoff-only@example.com",
                           names=["Handoff Only"])
        s.add(owner)
        s.commit()
        mbx.owner_person_id = owner.id
        s.commit()

        r = client.get(f"/api/network-map/{mid}")
        assert r.status_code == 200
        body = r.json()
        assert body["owner"]["canonical_email"] == "handoff-only@example.com"
        assert body["nodes"] == [] and body["edges"] == []
    finally:
        s.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.owner_email == "handoff-only@example.com"))
        s.commit()
        s.close()
