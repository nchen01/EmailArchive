"""S39 — recipient handoff project grouping / snapshot coverage labels (DB-gated).

Proves the label is FROZEN onto handoff_claim at generate time from the owner's
project table, surfaced on the recipient DTO, and read snapshot-only: mutating or
deleting the live Project/Event/Message/Thread rows after publish never changes the
recipient's labels. Also proves an excluded-only project contributes no label and
that no excluded content / links / ids leak.

All Gmail/LLM-free; deterministic. Grouping *rendering* is frontend (validated by a
standalone node mirror of buildRecipientAreas); this file proves the payload.
"""
from __future__ import annotations

import json as _json
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

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
pytestmark = requires_db

OWNER = "owner@acme.corp"
_TS = datetime(2026, 4, 15, tzinfo=timezone.utc)


# ── seed helpers ─────────────────────────────────────────────────────────────

def _seed_thread(session, mailbox_id, thread_id, messages):
    from services.db import models as orm
    session.add(orm.Thread(
        id=thread_id, mailbox_id=mailbox_id, subject_norm="s", t_start=_TS, t_end=_TS,
    ))
    session.flush()
    for m in messages:
        session.add(orm.Message(
            mailbox_id=mailbox_id, message_id_header=m["header"], provider_id=m["header"],
            thread_id=thread_id, sender_email=m.get("sender", "alice@ext.com"), ts=_TS,
            subject=m.get("subject", "subj"), clean_text=m.get("body", "body text here"),
            sensitivity=m.get("sensitivity", ["none"]), noise=m.get("noise", False),
        ))
    session.commit()


def _seed_project(session, mailbox_id, label):
    from services.db import models as orm
    p = orm.Project(
        mailbox_id=mailbox_id, label=label, label_source="ctfidf",
        start=_TS, end=_TS, confidence=0.9,
    )
    session.add(p)
    session.commit()
    return str(p.id)


def _seed_event(session, mailbox_id, actor_pid, headers, *, project_id=None,
                type_="did", summary="did the thing"):
    from services.db import models as orm
    session.add(orm.Event(
        mailbox_id=mailbox_id, actor_person_id=actor_pid, type=type_, summary=summary,
        source_message_ids=headers, confidence=0.9, project_id=project_id,
    ))
    session.commit()


@pytest.fixture()
def env():
    from fastapi.testclient import TestClient

    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from sqlalchemy import select

    session = SessionLocal()
    mbx = orm.Mailbox(provider="gmail", owner_email=OWNER, embed_model="deferred",
                      embed_dim=0, config={})
    session.add(mbx)
    session.commit()
    mid = str(mbx.id)
    owner = orm.Person(mailbox_id=mid, canonical_email=OWNER, names=["Owner"])
    session.add(owner)
    session.commit()
    mbx.owner_person_id = owner.id
    session.commit()

    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, session=session, mid=mid, owner_pid=str(owner.id))
    finally:
        session.rollback()
        pkg_ids = select(orm.HandoffPackage.id).where(orm.HandoffPackage.mailbox_id == mid)
        session.execute(orm.HandoffAuditEvent.__table__.delete().where(
            orm.HandoffAuditEvent.package_id.in_(pkg_ids)
        ))
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        session.commit()
        session.close()


def _fresh():
    from services.db.engine import SessionLocal
    return SessionLocal()


def _generate_two_project_package(env):
    """Seed two labeled projects (each a clean thread + event) → generate a package.

    Returns (package_id, {label: header}).
    """
    nexus = _seed_project(env.session, env.mid, "Nexus Auth Platform")
    security = _seed_project(env.session, env.mid, "Security Audit Remediation")
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "nexus-1@acme.corp", "subject": "Nexus", "body": "Shipped SSO."}])
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "sec-1@acme.corp", "subject": "Security", "body": "Rotated keys."}])
    _seed_event(env.session, env.mid, env.owner_pid, ["nexus-1@acme.corp"],
                project_id=nexus, summary="Shipped the Nexus SSO cutover")
    _seed_event(env.session, env.mid, env.owner_pid, ["sec-1@acme.corp"],
                project_id=security, summary="Rotated the remaining service keys")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 200
    return pid, {"Nexus Auth Platform": "nexus-1@acme.corp",
                 "Security Audit Remediation": "sec-1@acme.corp"}


def _publish_and_open(env, pid):
    code = env.client.post(f"/api/handoff/{pid}/publish",
                           json={"recipient_email": "cover@acme.corp"}).json()["capability_code"]
    token = env.client.post("/api/handoff/recipient/session", json={"code": code}).json()["session_token"]
    return token


def _recipient_get(env, token):
    return env.client.get("/api/handoff/recipient/package",
                          headers={"Authorization": f"Bearer {token}"})


# ── tests ────────────────────────────────────────────────────────────────────

def test_generate_freezes_project_label_on_claims(env):
    from services.db import models as orm
    pid, _ = _generate_two_project_package(env)
    s = _fresh()
    try:
        rows = s.execute(
            orm.HandoffClaim.__table__.select().where(orm.HandoffClaim.package_id == pid)
        ).all()
    finally:
        s.close()
    labels = sorted(r.project_label for r in rows)
    assert labels == ["Nexus Auth Platform", "Security Audit Remediation"]
    assert all(r.project_label is not None for r in rows)


def test_recipient_payload_returns_project_label(env):
    pid, _ = _generate_two_project_package(env)
    token = _publish_and_open(env, pid)
    body = _recipient_get(env, token).json()
    got = sorted(c["project_label"] for c in body["claims"])
    assert got == ["Nexus Auth Platform", "Security Audit Remediation"]
    # groupable into two distinct project groups from the snapshot alone
    assert len({c["project_label"] for c in body["claims"]}) == 2


def test_recipient_labels_frozen_after_project_rows_mutated(env):
    """Snapshot-only: change AND delete live Project rows after publish; the
    recipient's labels are unchanged (never re-resolved live)."""
    from services.db import models as orm
    pid, _ = _generate_two_project_package(env)
    token = _publish_and_open(env, pid)
    before = sorted(c["project_label"] for c in _recipient_get(env, token).json()["claims"])

    s = _fresh()
    try:
        # Rename one project, delete the other outright.
        for p in s.execute(orm.Project.__table__.select().where(
                orm.Project.mailbox_id == env.mid)).all():
            if p.label == "Nexus Auth Platform":
                s.execute(orm.Project.__table__.update()
                          .where(orm.Project.id == p.id).values(label="RENAMED LIVE"))
            else:
                s.execute(orm.Project.__table__.delete().where(orm.Project.id == p.id))
        s.commit()
    finally:
        s.close()

    after = sorted(c["project_label"] for c in _recipient_get(env, token).json()["claims"])
    assert after == before == ["Nexus Auth Platform", "Security Audit Remediation"]


def test_recipient_package_local_after_live_tables_wiped(env):
    """Delete every Project/Event/Message/Thread row for the mailbox after publish;
    the recipient package still renders with its frozen labels — proving the route
    reads only handoff_* snapshot rows."""
    from services.db import models as orm
    pid, _ = _generate_two_project_package(env)
    token = _publish_and_open(env, pid)
    s = _fresh()
    try:
        for model in (orm.Event, orm.Message, orm.Thread, orm.Project):
            s.execute(model.__table__.delete().where(model.mailbox_id == env.mid))
        s.commit()
    finally:
        s.close()
    r = _recipient_get(env, token)
    assert r.status_code == 200
    labels = sorted(c["project_label"] for c in r.json()["claims"])
    assert labels == ["Nexus Auth Platform", "Security Audit Remediation"]


def test_null_project_label_when_claim_has_no_project(env):
    """An event with no project_id yields a claim whose project_label is NULL — the
    recipient degrades to the coverageAreas fallback for those (frontend)."""
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "np-1@acme.corp", "subject": "General", "body": "Misc note."}])
    _seed_event(env.session, env.mid, env.owner_pid, ["np-1@acme.corp"],
                project_id=None, summary="Some unassigned decision")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 200
    token = _publish_and_open(env, pid)
    body = _recipient_get(env, token).json()
    assert len(body["claims"]) == 1
    assert body["claims"][0]["project_label"] is None


def test_excluded_only_project_has_no_label_and_no_leak(env):
    """A whole-thread-sensitive project contributes NO surviving claim, so its label
    never appears, and none of its excluded content / headers leak to the recipient."""
    from services.db import models as orm
    safe = _seed_project(env.session, env.mid, "Nexus Auth Platform")
    secret = _seed_project(env.session, env.mid, "Layoffs Planning")
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "nexus-1@acme.corp", "subject": "Nexus", "body": "Shipped SSO."}])
    # A sensitive whole thread → excluded before snapshotting.
    _seed_thread(env.session, env.mid, str(uuid.uuid4()), [
        {"header": "secret-1@acme.corp", "subject": "Layoff list",
         "body": "CONFIDENTIAL layoff names", "sensitivity": ["hr"]},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["nexus-1@acme.corp"],
                project_id=safe, summary="Shipped the Nexus SSO cutover")
    _seed_event(env.session, env.mid, env.owner_pid, ["secret-1@acme.corp"],
                project_id=secret, summary="Finalized the layoff list")

    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 200

    # Claim row: only the safe project's label was frozen; the excluded one never
    # produced a claim, so it has no label anywhere.
    s = _fresh()
    try:
        labels = [r.project_label for r in s.execute(
            orm.HandoffClaim.__table__.select().where(orm.HandoffClaim.package_id == pid)
        ).all()]
    finally:
        s.close()
    assert labels == ["Nexus Auth Platform"]

    token = _publish_and_open(env, pid)
    raw = _recipient_get(env, token).text
    assert "Layoffs Planning" not in raw           # excluded project label absent
    assert "layoff" not in raw.lower()             # excluded content/body absent
    assert "secret-1@acme.corp" not in raw          # excluded source header absent
    body = _json.loads(raw)
    assert [c["project_label"] for c in body["claims"]] == ["Nexus Auth Platform"]
    # Snapshot-only posture: no ids/links/tokens leak.
    for e in body["evidence"]:
        assert "open_url" not in e
    assert "mailbox_id" not in raw and "vault" not in raw.lower() and "token" not in raw.lower()


def test_return_delta_package_also_freezes_coverer_side_labels(env):
    """A return_delta package's claims freeze the coverer-side project label the same
    way (shared generator), so returns group cleanly by frozen labels."""
    from services.db import models as orm
    pid, _ = _generate_two_project_package(env)
    s = _fresh()
    try:
        s.execute(orm.HandoffPackage.__table__.update()
                  .where(orm.HandoffPackage.id == pid).values(package_type="return_delta"))
        s.commit()
    finally:
        s.close()
    # Regenerate under return_delta → labels still frozen.
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 200
    s = _fresh()
    try:
        rows = s.execute(
            orm.HandoffClaim.__table__.select().where(orm.HandoffClaim.package_id == pid)
        ).all()
        pkg_type = s.get(orm.HandoffPackage, pid).package_type
    finally:
        s.close()
    assert pkg_type == "return_delta"
    assert sorted(r.project_label for r in rows) == [
        "Nexus Auth Platform", "Security Audit Remediation",
    ]
