"""Track-B synthesis tests (S4 ticket 4.10, DoD §10).

DB-free unit tests cover the citation validator and the synthesis functions with
an injected deterministic ``synth_fn``. DB-gated API tests cover the 503 (key
absent) and 404 (bad id) paths.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ekc_schemas import (
    Edge,
    Event,
    EventType,
    LabelSource,
    Person,
    Project,
    Role,
    Thread,
)

from services.synthesis.contact_summary import synthesize_contact
from services.synthesis.contracts import SynthesisClaim, SynthesisResult
from services.synthesis.project_summary import synthesize_project

TS = datetime(2026, 4, 1, tzinfo=timezone.utc)


# ── DB-free unit tests ───────────────────────────────────────────────────────

def test_citation_validator_rejects_empty():
    """A SynthesisClaim with no citations is rejected (no citation, no claim)."""
    with pytest.raises(ValidationError):
        SynthesisClaim(text="we shipped it", source_message_ids=[])


def _fake_synth_fn(system: str, context: str, query: str) -> SynthesisResult:
    """Deterministic fake: emits one cited claim referencing a fixed header."""
    return SynthesisResult(
        claims=[
            SynthesisClaim(text="Coordinated the cutover", source_message_ids=["<a@x>"])
        ],
        model="fake-model",
        usage={"cache_read_input_tokens": 0},
    )


def _project() -> Project:
    return Project(
        id="p1",
        label="Atlas Migration",
        label_source=LabelSource.CTFIDF,
        thread_ids=["t1"],
        start=TS,
        end=TS,
        confidence=0.8,
    )


def _thread() -> Thread:
    return Thread(
        id="t1",
        subject_norm="Atlas cutover",
        participants=["alex@acme.com", "grace@acme.com"],
        t_start=TS,
        t_end=TS,
    )


def _event() -> Event:
    return Event(
        id="e1",
        actor_person_id="person-1",
        type=EventType.OUTCOME,
        summary="Staging cutover completed",
        project_id="p1",
        source_message_ids=["<a@x>"],
        confidence=0.6,
    )


def test_synthesize_project_empty_events():
    """Empty events AND threads → empty claims, no error, no model call."""
    result = synthesize_project(
        _project(), [], [], {}, synth_fn=_fake_synth_fn
    )
    assert result.claims == []
    assert result.state == "no evidenced activity in email"


def test_synthesize_project_with_events_cited():
    """With events/threads, the fake synth_fn produces cited claims."""
    result = synthesize_project(
        _project(), [_event()], [_thread()], {"t1": []}, synth_fn=_fake_synth_fn
    )
    assert len(result.claims) == 1
    assert result.claims[0].source_message_ids == ["<a@x>"]


def test_synthesize_contact_returns_claims():
    """With a fake synth_fn the contact result carries cited claims."""
    person = Person(
        id="person-1", canonical_email="grace@acme.com", names=["Grace Mueller"],
        role=Role.INTERNAL, role_confidence=0.9,
    )
    edge = Edge(
        person_id="person-1", message_count=5, sent_to_count=2, received_count=3,
        first_contact=TS, last_contact=TS, weight=0.7,
    )
    result = synthesize_contact(
        person, edge, [_thread()], [_event()], synth_fn=_fake_synth_fn
    )
    assert len(result.claims) == 1
    assert result.claims[0].source_message_ids


# ── DB-gated API tests ───────────────────────────────────────────────────────

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

    import json
    from pathlib import Path

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


@requires_db
def test_api_synthesis_503_no_key(seeded, no_api_key):
    """A project with events but no API key → 503, not 500."""
    client, mailbox_id, result, session = seeded
    from services.db import models as orm

    # Find a project that has at least one event.
    pid_with_event = session.execute(
        orm.Event.__table__.select().where(orm.Event.mailbox_id == mailbox_id)
    ).first()
    assert pid_with_event is not None, "fixture should have produced events"
    project_id = str(pid_with_event.project_id)

    r = client.post(f"/api/synthesis/{mailbox_id}/project/{project_id}")
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()


@requires_db
def test_api_synthesis_404_bad_project(seeded):
    client, mailbox_id, _, _ = seeded
    r = client.post(
        f"/api/synthesis/{mailbox_id}/project/00000000-0000-0000-0000-000000000000"
    )
    assert r.status_code == 404


@requires_db
def test_api_synthesis_404_bad_contact(seeded):
    client, mailbox_id, _, _ = seeded
    r = client.post(
        f"/api/synthesis/{mailbox_id}/contact/00000000-0000-0000-0000-000000000000"
    )
    assert r.status_code == 404


@requires_db
def test_api_synthesis_project_empty_events_graceful(seeded, no_api_key):
    """A project with no events returns an empty result, not an error."""
    client, mailbox_id, result, session = seeded
    from services.db import models as orm

    # Find a project with NO events.
    all_projects = {str(p.id) for p in result.clustering.projects}
    projects_with_events = {
        str(row.project_id)
        for row in session.execute(
            orm.Event.__table__.select().where(orm.Event.mailbox_id == mailbox_id)
        )
        if row.project_id is not None
    }
    empty = sorted(all_projects - projects_with_events)
    if not empty:
        pytest.skip("no event-free project in fixture")
    r = client.post(f"/api/synthesis/{mailbox_id}/project/{empty[0]}")
    # Empty events but non-empty threads still needs a key → 503; only fully
    # empty (no threads) short-circuits. Accept either graceful path.
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert r.json()["claims"] == []
