"""Round-trip + idempotency tests for the DB persistence layer (spec 04 DoD).

Requires a reachable Postgres (DATABASE_URL). Skipped otherwise so the in-memory
gates still run locally without Docker. Start the DB with:
    docker compose up -d && alembic upgrade head
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select

from conftest import mailbox_path, run_full_ingest

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
def session():
    from services.db.engine import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def mailbox_id(session):
    """Create an isolated mailbox row; tear it down (cascades) after the test."""
    from services.db import models as orm

    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=OWNER_EMAIL,
        embed_model="test-none",
        embed_dim=0,
        config={"internal_domains": INTERNAL_DOMAINS},
    )
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)
    yield mid
    session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
    session.commit()


def _count(session, model, mailbox_id) -> int:
    return session.execute(
        select(func.count()).select_from(model).where(model.mailbox_id == mailbox_id)
    ).scalar_one()


def test_l0_roundtrip_and_idempotency(session, mailbox_id):
    from services.db import models as orm
    from services.db.mappers import row_to_message, row_to_thread
    from services.db.store import persist_l0

    store = run_full_ingest()
    persist_l0(store, mailbox_id, session)

    # Row counts match the in-memory store.
    assert _count(session, orm.Message, mailbox_id) == len(store.messages)
    assert _count(session, orm.Thread, mailbox_id) == len(store.threads)

    # Round-trip a message: reconstruct via mappers and compare to original.
    original = {m.message_id_header: m for m in store.messages}
    msg_rows = session.execute(
        select(orm.Message).where(orm.Message.mailbox_id == mailbox_id)
    ).scalars().all()
    for row in msg_rows:
        atts = session.execute(
            select(orm.MessageAttachment).where(
                orm.MessageAttachment.message_id == row.id
            )
        ).scalars().all()
        rebuilt = row_to_message(row, atts)
        orig = original[row.message_id_header]
        assert rebuilt == orig

    # Round-trip a thread.
    orig_threads = {t.id: t for t in store.threads}
    thread_rows = session.execute(
        select(orm.Thread).where(orm.Thread.mailbox_id == mailbox_id)
    ).scalars().all()
    for row in thread_rows:
        rebuilt = row_to_thread(row, message_ids=orig_threads[str(row.id)].message_ids)
        assert rebuilt == orig_threads[str(row.id)]

    # Idempotency: re-persist, assert no new rows.
    before_msgs = _count(session, orm.Message, mailbox_id)
    before_threads = _count(session, orm.Thread, mailbox_id)
    persist_l0(store, mailbox_id, session)
    assert _count(session, orm.Message, mailbox_id) == before_msgs
    assert _count(session, orm.Thread, mailbox_id) == before_threads


def test_l1_persist_and_roundtrip(session, mailbox_id):
    from services.db import models as orm
    from services.db.mappers import row_to_edge, row_to_person
    from services.db.store import persist_l0, persist_l1
    from services.enrich.pipeline import run_enrichment

    store = run_full_ingest()
    persist_l0(store, mailbox_id, session)  # threads needed for FK / context

    result = run_enrichment(
        store.messages, owner_email=OWNER_EMAIL, internal_domains=INTERNAL_DOMAINS
    )
    persist_l1(result, mailbox_id, session)

    # All persons + edges persisted.
    assert _count(session, orm.Person, mailbox_id) == len(result.people)
    assert _count(session, orm.Edge, mailbox_id) == len(result.edges)

    # Round-trip a person (identities reattached from the identity table).
    orig_people = {p.id: p for p in result.people}
    person_rows = session.execute(
        select(orm.Person).where(orm.Person.mailbox_id == mailbox_id)
    ).scalars().all()
    for row in person_rows:
        idents = sorted(
            session.execute(
                select(orm.Identity.email).where(
                    orm.Identity.mailbox_id == mailbox_id,
                    orm.Identity.person_id == row.id,
                )
            ).scalars()
        )
        rebuilt = row_to_person(row, identities=idents)
        assert rebuilt == orig_people[str(row.id)]

    # Round-trip an edge.
    orig_edges = {e.person_id: e for e in result.edges}
    edge_rows = session.execute(
        select(orm.Edge).where(orm.Edge.mailbox_id == mailbox_id)
    ).scalars().all()
    for row in edge_rows:
        rebuilt = row_to_edge(row)
        assert rebuilt == orig_edges[str(row.person_id)]

    # Idempotency for L1.
    before = _count(session, orm.Person, mailbox_id), _count(session, orm.Edge, mailbox_id)
    persist_l1(result, mailbox_id, session)
    assert (
        _count(session, orm.Person, mailbox_id),
        _count(session, orm.Edge, mailbox_id),
    ) == before


def test_event_check_rejects_empty_sources(session, mailbox_id):
    """spec 04 DoD: event with empty source_message_ids is rejected by the DB CHECK."""
    from sqlalchemy.exc import IntegrityError

    from services.db import models as orm

    # Need a person to satisfy the actor FK.
    person = orm.Person(
        id=str(uuid.uuid4()),
        mailbox_id=mailbox_id,
        canonical_email="x@acme.com",
        names=["X"],
    )
    session.add(person)
    session.commit()

    bad = orm.Event(
        id=str(uuid.uuid4()),
        mailbox_id=mailbox_id,
        actor_person_id=person.id,
        type="did",
        summary="no citation",
        source_message_ids=[],
        confidence=0.5,
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
