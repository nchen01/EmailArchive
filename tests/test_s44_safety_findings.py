"""S44 - pre-publish privacy/safety review gates.

DB-free: the deterministic finding detector (each category + severity, no sensitive
text in explanations, determinism, Luhn payment, personal domain). DB-gated: the
creator package DTO surfaces findings, HIGH findings block publish, an audited
reason-override publishes, removing the flagged evidence + regenerate resolves the
finding, and the recipient payload never exposes findings.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.handoff.safety import Finding, high_severity, scan_package

DATABASE_URL = os.environ.get("DATABASE_URL")
_TS = datetime(2026, 4, 15, tzinfo=timezone.utc)


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

AKIA = "AKIAIOSFODNN7EXAMPLE"  # AWS's published EXAMPLE key (not a real secret)
SSN = "123-45-6789"
CARD = "4111111111111111"  # Visa test number (Luhn-valid)


def _claim(cid, kind, text, confidence=0.9):
    return {"id": cid, "kind": kind, "text": text, "confidence": confidence}


def _ev(header, subject="", body="", domain="acme.dev"):
    return {"header": header, "subject": subject, "body": body, "sender_domain": domain}


# -- DB-free detector ---------------------------------------------------------

def test_scan_detects_each_category_and_severity():
    claims = [
        _claim("c1", "decision", f"Set the key {AKIA} in CI."),
        _claim("c2", "decision", f"Card on file {CARD} for the account."),
        _claim("c3", "decision", f"Employee SSN {SSN} noted."),
        _claim("c4", "decision", "Discussed the severance and compensation package."),
        _claim("c5", "decision", "Patched the vulnerability from the data breach."),
        _claim("c6", "decision", "Notes on the diagnosis and prescription."),
        _claim("c7", "open_loop", "Blocked on the vendor sign-off."),
        _claim("c8", "decision", "We are switching the datastore from Postgres to DynamoDB."),
        _claim("c9", "open_loop", "Maybe revisit this.", confidence=0.2),
    ]
    cats = {(f.category, f.severity) for f in scan_package(claims, [])}
    assert ("credential_or_secret", "high") in cats
    assert ("payment_financial", "high") in cats
    assert ("personal_sensitive", "high") in cats            # SSN
    assert ("hr_legal", "medium") in cats
    assert ("security_sensitive", "medium") in cats
    assert ("personal_sensitive", "medium") in cats          # medical
    assert ("blocker_or_dependency", "medium") in cats
    assert ("stale_or_conflicting", "medium") in cats
    assert ("low_confidence_or_needs_confirmation", "low") in cats


def test_explanations_never_contain_matched_sensitive_text():
    claims = [_claim("c1", "decision", f"key {AKIA} ssn {SSN} card {CARD}")]
    for f in scan_package(claims, []):
        assert AKIA not in f.explanation
        assert SSN not in f.explanation
        assert CARD not in f.explanation


def test_payment_requires_luhn_valid_number():
    # A 16-digit non-Luhn run must NOT be flagged as payment.
    good = scan_package([_claim("c", "decision", f"card {CARD}")], [])
    bad = scan_package([_claim("c", "decision", "ticket 1234567890123456 ref")], [])
    assert any(f.category == "payment_financial" for f in good)
    assert not any(f.category == "payment_financial" for f in bad)


def test_personal_domain_from_evidence_sender():
    findings = scan_package([], [_ev("m@x", body="hi", domain="gmail.com")])
    assert any(f.category == "personal_sensitive" and f.severity == "medium" for f in findings)


def test_scan_is_deterministic_and_high_helper():
    claims = [_claim("c1", "decision", f"key {AKIA}"), _claim("c2", "decision", "benign")]
    a = scan_package(claims, [])
    b = scan_package(claims, [])
    assert [(_f.id, _f.category) for _f in a] == [(_f.id, _f.category) for _f in b]
    assert [f.category for f in high_severity(a)] == ["credential_or_secret"]


def test_benign_package_has_no_findings():
    claims = [_claim("c1", "decision", "Shipped the cutover to production.")]
    ev = [_ev("m@x", "Cutover", "Cutover shipped.")]
    assert scan_package(claims, ev) == []


# -- DB-gated: creator DTO + publish gate -------------------------------------

OWNER = "owner@acme.corp"


def _seed_thread(session, mid, tid, messages):
    from services.db import models as orm
    session.add(orm.Thread(id=tid, mailbox_id=mid, subject_norm="s", t_start=_TS, t_end=_TS))
    session.flush()
    for m in messages:
        session.add(orm.Message(
            mailbox_id=mid, message_id_header=m["header"], provider_id=m["header"],
            thread_id=tid, sender_email=m.get("sender", "a@acme.corp"), ts=_TS,
            subject=m.get("subject", "s"), clean_text=m["body"],
            sensitivity=m.get("sensitivity", ["none"]), noise=m.get("noise", False),
        ))
    session.commit()


def _seed_event(session, mid, pid, headers, summary, type_="did"):
    from services.db import models as orm
    session.add(orm.Event(mailbox_id=mid, actor_person_id=pid, type=type_,
                          summary=summary, source_message_ids=headers, confidence=0.9))
    session.commit()


@pytest.fixture()
def env():
    from fastapi.testclient import TestClient
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from sqlalchemy import select

    session = SessionLocal()
    mbx = orm.Mailbox(provider="gmail", owner_email=OWNER, embed_model="deferred", embed_dim=0, config={})
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
            orm.HandoffAuditEvent.package_id.in_(pkg_ids)))
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        session.commit()
        session.close()


def _high_risk_generated(env):
    """Seed a credential-leaking (non-sensitive) thread + event, create + generate."""
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "risk-1@acme.corp", "subject": "CI deploy",
                   "body": f"Set the deploy key {AKIA} in the CI config."}])
    _seed_event(env.session, env.mid, env.owner_pid, ["risk-1@acme.corp"],
                "Configured the CI deploy with the AWS access key")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 200
    return pid


@requires_db
def test_creator_dto_surfaces_high_finding_and_publish_is_blocked(env):
    pid = _high_risk_generated(env)
    body = env.client.get(f"/api/handoff/{pid}").json()
    highs = [f for f in body["findings"] if f["severity"] == "high"]
    assert any(f["category"] == "credential_or_secret" for f in highs)
    # explanation carries no matched text
    assert all(AKIA not in f["explanation"] for f in body["findings"])

    r = env.client.post(f"/api/handoff/{pid}/publish", json={"recipient_email": "cov@acme.corp"})
    assert r.status_code == 422 and "high-severity" in r.json()["detail"]


@requires_db
def test_audited_override_publishes_with_safe_metadata(env):
    from services.db import models as orm
    pid = _high_risk_generated(env)
    high_ids = [f["id"] for f in env.client.get(f"/api/handoff/{pid}").json()["findings"]
                if f["severity"] == "high"]
    r = env.client.post(f"/api/handoff/{pid}/publish", json={
        "recipient_email": "cov@acme.corp",
        "safety_ack": {"reason": "known example key, false positive", "acknowledged_finding_ids": high_ids},
    })
    assert r.status_code == 200

    from sqlalchemy import select
    from services.db.engine import SessionLocal
    db = SessionLocal()
    try:
        rows = db.execute(
            select(orm.HandoffAuditEvent).where(orm.HandoffAuditEvent.package_id == pid)
        ).scalars().all()
    finally:
        db.close()
    override = [r for r in rows if r.action == "package_published_with_safety_override"]
    assert len(override) == 1
    meta = override[0].metadata_
    assert "credential_or_secret" in meta["finding_categories"]
    assert meta["high_finding_count"] == 1
    assert meta["reason"] == "known example key, false positive"
    # SAFE metadata only: never the matched sensitive text.
    import json as _json
    assert AKIA not in _json.dumps(meta)


@requires_db
def test_blank_reason_or_missing_ack_still_blocks(env):
    pid = _high_risk_generated(env)
    high_ids = [f["id"] for f in env.client.get(f"/api/handoff/{pid}").json()["findings"]
                if f["severity"] == "high"]
    # blank reason
    assert env.client.post(f"/api/handoff/{pid}/publish", json={
        "recipient_email": "cov@acme.corp",
        "safety_ack": {"reason": "   ", "acknowledged_finding_ids": high_ids},
    }).status_code == 422
    # ack does not cover the high finding
    assert env.client.post(f"/api/handoff/{pid}/publish", json={
        "recipient_email": "cov@acme.corp",
        "safety_ack": {"reason": "ok", "acknowledged_finding_ids": ["nope"]},
    }).status_code == 422


@requires_db
def test_removing_evidence_resolves_finding_and_allows_publish(env):
    pid = _high_risk_generated(env)
    # Exclude the credential-carrying message, then regenerate: the claim loses its
    # only citation and drops, so the credential finding is gone.
    scope = env.client.get(f"/api/handoff/{pid}").json()["scope"]
    scope["excluded_message_id_headers"] = ["risk-1@acme.corp"]
    assert env.client.patch(f"/api/handoff/{pid}/scope", json=scope).status_code == 200
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 200

    body = env.client.get(f"/api/handoff/{pid}").json()
    assert not any(f["severity"] == "high" for f in body["findings"])
    # With no evidence at all the package cannot publish; add a benign event so it can.
    _seed_thread(env.session, env.mid, str(uuid.uuid4()),
                 [{"header": "ok-1@acme.corp", "subject": "Cutover", "body": "Shipped the cutover."}])
    _seed_event(env.session, env.mid, env.owner_pid, ["ok-1@acme.corp"], "Shipped the cutover")
    # keep the credential message excluded across the regenerate
    scope = env.client.get(f"/api/handoff/{pid}").json()["scope"]
    scope["excluded_message_id_headers"] = ["risk-1@acme.corp"]
    env.client.patch(f"/api/handoff/{pid}/scope", json=scope)
    env.client.post(f"/api/handoff/{pid}/generate")
    body = env.client.get(f"/api/handoff/{pid}").json()
    assert not any(f["severity"] == "high" for f in body["findings"])
    r = env.client.post(f"/api/handoff/{pid}/publish", json={"recipient_email": "cov@acme.corp"})
    assert r.status_code == 200


@requires_db
def test_recipient_payload_never_exposes_findings(env):
    pid = _high_risk_generated(env)
    high_ids = [f["id"] for f in env.client.get(f"/api/handoff/{pid}").json()["findings"]
                if f["severity"] == "high"]
    code = env.client.post(f"/api/handoff/{pid}/publish", json={
        "recipient_email": "cov@acme.corp",
        "safety_ack": {"reason": "ack", "acknowledged_finding_ids": high_ids},
    }).json()["capability_code"]
    token = env.client.post("/api/handoff/recipient/session", json={"code": code}).json()["session_token"]
    body = env.client.get("/api/handoff/recipient/package",
                          headers={"Authorization": f"Bearer {token}"}).json()
    assert "findings" not in body


@requires_db
def test_harness_high_risk_scenario_is_flagged_and_benign_are_not():
    from services.handoff.eval.corpus import load_corpus
    from services.handoff.eval.harness import run_scenario
    from services.db.engine import SessionLocal
    s = SessionLocal()
    try:
        for sc in load_corpus():
            r = run_scenario(s, sc.data)
            assert r.quality["expected_findings_match"], f"{sc.name}: {r.quality}"
            if sc.name == "high_risk_content":
                assert r.quality["high_severity_finding_present"] is True
            else:
                assert r.quality["high_severity_finding_present"] is False
    finally:
        s.close()
