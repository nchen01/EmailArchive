"""API tests for the project-view endpoints (spec 02 §5, §6).

DB-gated like the network-map tests: requires a reachable Postgres; skipped
otherwise. Also includes a DB-free unit test for the state derivation.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from services.api.routers.project_view import project_state

NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


# ── DB-free unit tests ───────────────────────────────────────────────────────

def test_project_state_active_recent():
    assert project_state([NOW - timedelta(days=5)], now=NOW) == "active"


def test_project_state_stale_old():
    assert project_state([NOW - timedelta(days=40)], now=NOW) == "stale"


def test_project_state_empty_is_stale():
    assert project_state([], now=NOW) == "stale"


def test_project_state_uses_latest_thread():
    ends = [NOW - timedelta(days=40), NOW - timedelta(days=2)]
    assert project_state(ends, now=NOW) == "active"


def test_project_state_shipping_with_outcome():
    assert project_state(
        [NOW - timedelta(days=5)], has_recent_outcome=True, now=NOW
    ) == "shipping"


# ── DB-backed integration tests ──────────────────────────────────────────────

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
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres unreachable.",
)

OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]


@pytest.fixture()
def seeded():
    from fastapi.testclient import TestClient

    from conftest import run_full_ingest
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import persist_l0, persist_l1
    from services.enrich.clustering.eval.run_eval import EVAL_PARAMS
    from services.enrich.clustering.testkit import FakeNlp, make_test_embed
    from services.enrich.pipeline import run_enrichment

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email=OWNER_EMAIL,
        embed_model="test-none", embed_dim=0,
        config={"internal_domains": INTERNAL_DOMAINS},
    )
    session.add(mbx)
    session.commit()
    mailbox_id = str(mbx.id)

    store = run_full_ingest()
    persist_l0(store, mailbox_id, session)
    result = run_enrichment(
        store.messages, OWNER_EMAIL, INTERNAL_DOMAINS,
        threads=store.threads, embed_fn=make_test_embed(), nlp=FakeNlp(),
        cluster_params=EVAL_PARAMS,
    )
    persist_l1(result, mailbox_id, session)

    client = TestClient(app)
    try:
        yield client, mailbox_id, result
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id))
        session.commit()
        session.close()


@requires_db
def test_list_projects(seeded):
    client, mailbox_id, result = seeded
    r = client.get(f"/api/projects/{mailbox_id}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["projects"]) == len(result.clustering.projects)
    # Sorted by confidence DESC.
    confs = [p["confidence"] for p in body["projects"]]
    assert confs == sorted(confs, reverse=True)
    for p in body["projects"]:
        assert p["state"] in {"active", "stale"}
        assert p["thread_count"] >= 1


@requires_db
def test_project_detail_shape(seeded):
    client, mailbox_id, result = seeded
    listing = client.get(f"/api/projects/{mailbox_id}").json()["projects"]
    # Pick the largest project for a rich detail.
    target = max(listing, key=lambda p: p["thread_count"])
    r = client.get(f"/api/projects/{mailbox_id}/{target['id']}")
    assert r.status_code == 200
    d = r.json()
    for key in (
        "id", "label", "state", "confidence", "start", "end", "metrics",
        "who_to_ask", "members", "recent_threads", "activity",
    ):
        assert key in d
    assert d["activity"] == []  # S4 deferred
    assert d["metrics"]["threads"] == target["thread_count"]
    assert len(d["who_to_ask"]) <= 4
    # Every recent thread has a real thread id + subject.
    for t in d["recent_threads"]:
        assert t["thread_id"]


@requires_db
def test_who_to_ask_role_labelled(seeded):
    client, mailbox_id, _ = seeded
    listing = client.get(f"/api/projects/{mailbox_id}").json()["projects"]
    target = max(listing, key=lambda p: p["member_count"])
    d = client.get(f"/api/projects/{mailbox_id}/{target['id']}").json()
    for c in d["who_to_ask"]:
        assert "role" in c and c["role"]
        assert "weight" in c


@requires_db
def test_unknown_project_404(seeded):
    client, mailbox_id, _ = seeded
    r = client.get(f"/api/projects/{mailbox_id}/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


@requires_db
def test_unknown_mailbox_404(seeded):
    client, _, _ = seeded
    r = client.get("/api/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
