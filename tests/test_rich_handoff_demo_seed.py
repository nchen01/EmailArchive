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
    not _db_reachable(), reason="DATABASE_URL not set or Postgres unreachable."
)


def test_rich_demo_dataset_shape_and_subjects_are_realistic():
    from scripts.seed_rich_handoff_demo import PROJECTS, build_rich_dataset

    threads, events = build_rich_dataset()
    messages = [m for t in threads for m in t["messages"]]
    headers = {m["header"] for m in messages}
    project_labels = {p.label for p in PROJECTS}

    assert len(PROJECTS) == 12
    assert len(threads) == 72
    assert 200 <= len(messages) <= 500
    assert len(messages) == len(headers)
    assert len(events) == len(threads)

    for thread in threads:
        label = thread["project"].label
        assert thread["subject"] not in project_labels
        for msg in thread["messages"]:
            assert msg["subject"] not in project_labels
            assert label in msg["body"]
            assert len(msg["body"]) > 180

    for event in events:
        assert event["type"] in {"did", "outcome", "proposed"}
        assert event["source_message_ids"]
        assert all(h in headers for h in event["source_message_ids"])

    assert any(t["sensitivity"] for t in threads)
    assert any(t["noise"] for t in threads)


@requires_db
def test_rich_demo_seed_materializes_projects_and_generates_package():
    from sqlalchemy import delete, select

    from scripts.seed_rich_handoff_demo import OWNER_EMAIL, seed_rich_mailbox, verify_rich_mailbox
    from services.db import models as orm
    from services.db.engine import SessionLocal

    session = SessionLocal()
    try:
        counts = seed_rich_mailbox(session)
        mailbox_id = counts["mailbox_id"]

        assert counts["projects"] == 12
        assert counts["threads"] == 72
        assert 200 <= counts["messages"] <= 500
        assert counts["events"] == 72
        assert counts["people"] >= 20
        assert counts["edges"] >= 15

        verify = verify_rich_mailbox(session, mailbox_id)
        assert verify["ok"] is True
        assert verify["claims"] >= 50
        assert verify["evidence"] >= 50
        assert verify["excluded_ok"] is True

        project_members = session.execute(
            select(orm.ProjectMember).where(
                orm.ProjectMember.project_id.in_(
                    select(orm.Project.id).where(orm.Project.mailbox_id == mailbox_id)
                )
            )
        ).scalars().all()
        assert project_members
    finally:
        session.rollback()
        mbx = session.execute(
            select(orm.Mailbox).where(orm.Mailbox.owner_email == OWNER_EMAIL)
        ).scalar_one_or_none()
        if mbx is not None:
            session.execute(delete(orm.Mailbox).where(orm.Mailbox.id == mbx.id))
            session.commit()
        session.close()
