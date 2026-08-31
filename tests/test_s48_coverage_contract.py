"""S48 - per-project coverage contract MVP (computed-only).

DB-free: the pure assembler (services/handoff/coverage_contract.py) groups by frozen
project_label, is citation-backed by construction, reconciles evidence, drops
uncited claims, orders named-then-fallback-then-other, and frames return mode.

DB-gated: the creator + recipient DTOs surface an additive coverage_contract block
assembled from the SAME frozen snapshot; the recipient contract is package-local
(survives wiping every live Project/Event/Message/Thread row) and anti-oracle (no
per-project exclusion counts / hidden-content categories; excluded content absent).
"""
from __future__ import annotations

import json as _json
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.handoff.coverage_contract import (
    OTHER_EVIDENCE_LABEL,
    UNASSIGNED_LABEL,
    build_coverage_contract,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
_TS = datetime(2026, 4, 15, tzinfo=timezone.utc)


# -- DB-free: the pure assembler ----------------------------------------------

def _c(cid, kind, text, label, cites):
    return {"id": cid, "kind": kind, "text": text, "project_label": label, "cites": cites}


def test_groups_by_frozen_label_named_then_fallback_then_other():
    claims = [
        _c("c1", "decision", "Chose Postgres", "Nexus Auth", ["<m1>"]),
        _c("c2", "open_loop", "Finish SSO", "Nexus Auth", ["<m2>"]),
        _c("c3", "blocker", "Waiting on legal", "Atlas", ["<m3>"]),
        _c("c4", "decision", "Unlabeled call", None, ["<m4>"]),
    ]
    headers = ["<m1>", "<m2>", "<m3>", "<m4>", "<m5>"]  # m5 cited by nobody
    entries = build_coverage_contract(claims, headers)
    labels = [e["project_label"] for e in entries]
    # Named A-Z, then unassigned fallback, then other-evidence last.
    assert labels == ["Atlas", "Nexus Auth", UNASSIGNED_LABEL, OTHER_EVIDENCE_LABEL]
    assert [e["is_fallback"] for e in entries] == [False, False, True, True]


def test_buckets_by_kind_and_counts_in_summary():
    claims = [
        _c("c1", "decision", "d", "P", ["<m1>"]),
        _c("c2", "open_loop", "o", "P", ["<m2>"]),
        _c("c3", "project_state", "status", "P", ["<m2>"]),  # folded into open loops
        _c("c4", "blocker", "b", "P", ["<m3>"]),
        _c("c5", "person_note", "ping", "P", ["<m3>"]),
    ]
    e = build_coverage_contract(claims, ["<m1>", "<m2>", "<m3>"])[0]
    assert len(e["decisions"]) == 1
    assert len(e["open_loops"]) == 2  # open_loop + project_state
    assert len(e["blockers"]) == 1
    assert len(e["people"]) == 1
    assert "1 decision" in e["covers_summary"]
    assert "2 open loops" in e["covers_summary"]
    assert "1 blocker" in e["covers_summary"]
    assert "1 person note" in e["covers_summary"]


def test_every_item_is_citation_backed_and_uncited_claim_dropped():
    claims = [
        _c("c1", "decision", "kept", "P", ["<m1>", "<offpkg>"]),
        _c("c2", "open_loop", "dropped - no in-package evidence", "P", ["<offpkg>"]),
    ]
    entries = build_coverage_contract(claims, ["<m1>"])
    items = [it for e in entries for k in ("decisions", "open_loops", "blockers", "people") for it in e[k]]
    # c2 is dropped entirely; c1 kept with only its in-package header.
    assert len(items) == 1
    assert items[0]["claim_id"] == "c1"
    assert items[0]["source_message_id_headers"] == ["<m1>"]
    assert all(it["source_message_id_headers"] for it in items)


def test_evidence_set_reconciles_via_other_evidence_bucket():
    claims = [_c("c1", "decision", "d", "P", ["<m1>"])]
    headers = ["<m1>", "<m2>", "<m3>"]  # m2,m3 cited by no claim
    entries = build_coverage_contract(claims, headers)
    union = {h for e in entries for h in e["evidence_refs"]}
    assert union == set(headers)  # nothing invented, nothing dropped
    other = [e for e in entries if e["project_label"] == OTHER_EVIDENCE_LABEL][0]
    assert sorted(other["evidence_refs"]) == ["<m2>", "<m3>"]


def test_safety_posture_is_neutral_booleans_only_no_counts():
    e = build_coverage_contract([_c("c1", "decision", "d", "P", ["<m1>"])], ["<m1>"])[0]
    assert e["safety_posture"] == {"scope_limited": True, "sensitive_excluded": True}
    # No exclusion count / category keys anywhere in the entry.
    blob = _json.dumps(build_coverage_contract([_c("c1", "decision", "d", "P", ["<m1>"])], ["<m1>"]))
    for banned in ("exclusion", "withheld", "excluded_count", "hidden"):
        assert banned not in blob.lower()


def test_return_mode_reframes_summary_and_boundary():
    entries = build_coverage_contract(
        [_c("c1", "decision", "d", "Nexus Auth", ["<m1>"])], ["<m1>"], return_mode=True
    )
    assert "while you were away" in entries[0]["covers_summary"]
    assert "coverage period" in entries[0]["boundary"].lower()


def test_empty_package_yields_empty_contract():
    assert build_coverage_contract([], []) == []


def test_deterministic_output():
    claims = [
        _c("c1", "decision", "d", "B", ["<m1>"]),
        _c("c2", "open_loop", "o", "A", ["<m2>"]),
    ]
    headers = ["<m1>", "<m2>"]
    assert build_coverage_contract(claims, headers) == build_coverage_contract(claims, headers)


# -- DB-gated: creator + recipient DTOs ---------------------------------------

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

OWNER = "owner@acme.corp"


def _seed_thread(session, mailbox_id, thread_id, messages):
    from services.db import models as orm
    session.add(orm.Thread(id=thread_id, mailbox_id=mailbox_id, subject_norm="s", t_start=_TS, t_end=_TS))
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
    p = orm.Project(mailbox_id=mailbox_id, label=label, label_source="ctfidf",
                    start=_TS, end=_TS, confidence=0.9)
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
    return pid


def _publish_and_open(env, pid):
    code = env.client.post(f"/api/handoff/{pid}/publish",
                           json={"recipient_email": "cover@acme.corp"}).json()["capability_code"]
    return env.client.post("/api/handoff/recipient/session", json={"code": code}).json()["session_token"]


def _recipient_get(env, token):
    return env.client.get("/api/handoff/recipient/package",
                          headers={"Authorization": f"Bearer {token}"})


@requires_db
def test_creator_dto_has_coverage_contract(env):
    pid = _generate_two_project_package(env)
    body = env.client.get(f"/api/handoff/{pid}").json()
    contract = body["coverage_contract"]
    labels = sorted(e["project_label"] for e in contract)
    assert labels == ["Nexus Auth Platform", "Security Audit Remediation"]
    # Every contract item is citation-backed by an in-package evidence header.
    ev_headers = {e["message_id_header"] for e in body["evidence"]}
    for entry in contract:
        for key in ("decisions", "open_loops", "blockers", "people"):
            for it in entry[key]:
                assert it["source_message_id_headers"]
                assert all(h in ev_headers for h in it["source_message_id_headers"])


@requires_db
def test_recipient_dto_has_matching_coverage_contract(env):
    pid = _generate_two_project_package(env)
    token = _publish_and_open(env, pid)
    body = _recipient_get(env, token).json()
    contract = body["coverage_contract"]
    assert sorted(e["project_label"] for e in contract) == [
        "Nexus Auth Platform", "Security Audit Remediation",
    ]
    # Contract evidence reconciles with the package evidence set.
    ev_headers = {e["message_id_header"] for e in body["evidence"]}
    union = {h for e in contract for h in e["evidence_refs"]}
    assert union == ev_headers


@requires_db
def test_recipient_contract_is_package_local_after_live_tables_wiped(env):
    """Delete every live Project/Event/Message/Thread row after publish; the
    recipient contract still renders from the frozen snapshot alone."""
    from services.db import models as orm
    pid = _generate_two_project_package(env)
    token = _publish_and_open(env, pid)
    before = sorted(e["project_label"] for e in _recipient_get(env, token).json()["coverage_contract"])
    s = _fresh()
    try:
        for model in (orm.Event, orm.Message, orm.Thread, orm.Project):
            s.execute(model.__table__.delete().where(model.mailbox_id == env.mid))
        s.commit()
    finally:
        s.close()
    r = _recipient_get(env, token)
    assert r.status_code == 200
    after = sorted(e["project_label"] for e in r.json()["coverage_contract"])
    assert after == before == ["Nexus Auth Platform", "Security Audit Remediation"]


@requires_db
def test_recipient_contract_anti_oracle_no_exclusion_counts_or_excluded_content(env):
    """A whole-thread-sensitive project is excluded before snapshotting. The
    recipient contract must not expose per-project exclusion counts/categories, the
    excluded label/content/headers, or any live link."""
    from services.db import models as orm
    safe = _seed_project(env.session, env.mid, "Nexus Auth Platform")
    secret = _seed_project(env.session, env.mid, "Layoffs Planning")
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "nexus-1@acme.corp", "subject": "Nexus", "body": "Shipped SSO."}])
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "secret-1@acme.corp", "subject": "Layoff list",
                   "body": "CONFIDENTIAL layoff names", "sensitivity": ["hr"]}])
    _seed_event(env.session, env.mid, env.owner_pid, ["nexus-1@acme.corp"],
                project_id=safe, summary="Shipped the Nexus SSO cutover")
    _seed_event(env.session, env.mid, env.owner_pid, ["secret-1@acme.corp"],
                project_id=secret, summary="Finalized the layoff list")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 200
    token = _publish_and_open(env, pid)
    raw = _recipient_get(env, token).text

    # The whole recipient payload (including the contract) leaks no excluded data.
    assert "Layoffs Planning" not in raw
    assert "layoff" not in raw.lower()
    assert "secret-1@acme.corp" not in raw
    body = _json.loads(raw)
    contract = body["coverage_contract"]
    # Only the safe project is present.
    assert [e["project_label"] for e in contract if not e["is_fallback"]] == ["Nexus Auth Platform"]
    # No exclusion-count / hidden-category signal on any entry.
    contract_blob = _json.dumps(contract).lower()
    for banned in ("exclusion", "withheld", "hidden", "excluded_count", "sensitivity"):
        assert banned not in contract_blob
    # Each entry's safety_posture is neutral booleans only.
    for e in contract:
        assert set(e["safety_posture"].keys()) == {"scope_limited", "sensitive_excluded"}
    # No live/source links or ids leak through the contract.
    assert "open_url" not in contract_blob and "mailbox_id" not in contract_blob


@requires_db
def test_creator_only_exclusion_posture_stays_off_the_contract(env):
    """The creator still gets aggregate exclusion_counts on the package DTO, but the
    coverage_contract block itself carries none (identical safe shape both sides)."""
    from services.db import models as orm
    safe = _seed_project(env.session, env.mid, "Nexus Auth Platform")
    secret = _seed_project(env.session, env.mid, "Layoffs Planning")
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "nexus-1@acme.corp", "subject": "Nexus", "body": "Shipped SSO."}])
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "secret-1@acme.corp", "subject": "Layoff list",
                   "body": "CONFIDENTIAL layoff names", "sensitivity": ["hr"]}])
    _seed_event(env.session, env.mid, env.owner_pid, ["nexus-1@acme.corp"],
                project_id=safe, summary="Shipped the Nexus SSO cutover")
    _seed_event(env.session, env.mid, env.owner_pid, ["secret-1@acme.corp"],
                project_id=secret, summary="Finalized the layoff list")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 200
    body = env.client.get(f"/api/handoff/{pid}").json()
    # Creator-only exclusion posture is present at the package level...
    assert isinstance(body["exclusion_counts"], dict)
    # ...but not inside the coverage_contract block.
    assert "exclusion" not in _json.dumps(body["coverage_contract"]).lower()
