"""S5 cover-for-me tests (D11, implementation-plan §6.3).

DB-gated like the other API tests: requires a reachable Postgres; skipped
otherwise. They seed the fixture mailbox (L0 + L1 + Events) and exercise the
routing surface:

  - fallback (no entity) → routed_to None, "insufficient" state, no model call.
  - project label in the query → routed_to "project:...".
  - person name in the query → routed_to "person:...".
  - missing API key on a routed query → 503.
  - bad mailbox id → 404.
"""
from __future__ import annotations

import os

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
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres unreachable.",
)

OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]


@pytest.fixture()
def seeded():
    """Seed a mailbox with L0 + L1 + extracted Events, yield a TestClient."""
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient

    from conftest import run_full_ingest
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import persist_l0, persist_l1
    from services.enrich.clustering.eval.run_eval import EVAL_PARAMS
    from services.enrich.clustering.testkit import FakeNlp, make_test_embed
    from services.enrich.events.eval.run_eval import make_gold_extract_fn
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

    gold = json.loads(
        (Path(__file__).resolve().parent.parent / "fixtures" / "gold" / "events.json")
        .read_text(encoding="utf-8")
    )
    extract_fn = make_gold_extract_fn(store, gold)

    result = run_enrichment(
        store.messages, OWNER_EMAIL, INTERNAL_DOMAINS,
        threads=store.threads, embed_fn=make_test_embed(), nlp=FakeNlp(),
        cluster_params=EVAL_PARAMS, extract_fn=extract_fn,
    )
    persist_l1(result, mailbox_id, session)

    client = TestClient(app)
    try:
        yield client, mailbox_id, result, session
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id))
        session.commit()
        session.close()


@pytest.fixture()
def no_api_key():
    """Ensure ANTHROPIC_API_KEY is absent for the duration of the test."""
    prev = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ["ANTHROPIC_API_KEY"] = prev


def _a_project_label(result) -> str:
    return result.clustering.projects[0].label


def _a_person_name(result) -> str:
    """A non-owner person name present in L1."""
    for p in result.people:
        if p.canonical_email.lower() == OWNER_EMAIL.lower():
            continue
        if p.names:
            return p.names[0]
    raise AssertionError("fixture should have a non-owner person with a name")


@requires_db
def test_fallback_no_match(seeded):
    """A query mentioning no known entity → routed_to None + insufficient state.

    This path short-circuits before any model call, so no API key is needed."""
    client, mailbox_id, _, _ = seeded
    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": "what is the meaning of life?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["routed_to"] is None
    assert "insufficient" in (body["result"]["state"] or "").lower()


@requires_db
def test_routes_to_project(seeded, no_api_key):
    """A query mentioning a known project label routes to the model stage.

    A matched entity must reach the model (503 without a key) rather than fall
    through to the keyless "insufficient evidence" fallback (which returns 200).
    The exact routed_to label is asserted in ``test_routes_to_project_label``."""
    client, mailbox_id, result, _ = seeded
    label = _a_project_label(result)
    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": f"what is the status of {label}?"},
    )
    # A matched project needs the model → 503 without a key (proves it routed,
    # not fell through to the keyless fallback which returns 200).
    assert r.status_code == 503


@requires_db
def test_routes_to_project_label(seeded):
    """Direct routing check: the resolved route is project:<label>."""
    from services.api.routers.cover_for_me import _route
    from services.db.engine import SessionLocal

    _, mailbox_id, result, _ = seeded
    label = _a_project_label(result)
    db = SessionLocal()
    try:
        person, project = _route(f"status of {label}", db, mailbox_id)
        assert project is not None and person is None
        assert project.label == label
    finally:
        db.close()


@requires_db
def test_routes_to_person(seeded):
    """Direct routing check: a known person name resolves to a person route."""
    from services.api.routers.cover_for_me import _route
    from services.db.engine import SessionLocal

    _, mailbox_id, result, _ = seeded
    name = _a_person_name(result)
    db = SessionLocal()
    try:
        person, project = _route(f"who do I ask about working with {name}", db, mailbox_id)
        assert person is not None and project is None
        assert name in person.names
    finally:
        db.close()


@requires_db
def test_missing_key_503(seeded, no_api_key):
    """A routed query with no API key → 503, not 500."""
    client, mailbox_id, result, _ = seeded
    name = _a_person_name(result)
    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": f"who do I ask about {name}?"},
    )
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()


@requires_db
def test_404_bad_mailbox(seeded):
    client, _, _, _ = seeded
    r = client.post(
        "/api/cover-for-me/00000000-0000-0000-0000-000000000000",
        json={"query": "anything"},
    )
    assert r.status_code == 404
