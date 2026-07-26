"""S17.3 — creator draft/scope/generate handoff package slice (DB-gated).

Covers: migration presence + a key constraint, create draft, scope update,
candidate generation from seeded L1 events, whole-thread sensitivity exclusion,
creator exclusions, claim-without-evidence drop, audit metadata safety, and
illegal state-transition rejection. All Gmail/LLM-free; deterministic.
"""
from __future__ import annotations

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

OWNER = "owner@acme.corp"
_TS = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _seed_thread(session, mailbox_id, thread_id, messages):
    """messages: list of dict(header, sensitivity=list, subject, body, sender)."""
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


def _seed_event(session, mailbox_id, actor_pid, headers, *, type_="did", summary="did the thing"):
    from services.db import models as orm
    session.add(orm.Event(
        mailbox_id=mailbox_id, actor_person_id=actor_pid, type=type_, summary=summary,
        source_message_ids=headers, confidence=0.9,
    ))
    session.commit()


@pytest.fixture()
def env():
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal

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
        # handoff_audit_event has no FK cascade (retained by design), so the
        # mailbox delete below won't remove it — clean it up explicitly for this
        # test's packages so ekc_test/dev runs don't accumulate rows. Production
        # retention behavior is unchanged.
        pkg_ids = select(orm.HandoffPackage.id).where(orm.HandoffPackage.mailbox_id == mid)
        session.execute(orm.HandoffAuditEvent.__table__.delete().where(
            orm.HandoffAuditEvent.package_id.in_(pkg_ids)
        ))
        session.execute(orm.Identity.__table__.delete().where(orm.Identity.mailbox_id == mid))
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        session.commit()
        session.close()


def _fresh():
    from services.db.engine import SessionLocal
    return SessionLocal()


# ── Audit sanitizer defends future callers (DB-free) ─────────────────────────

def test_safe_metadata_drops_contentlike_and_secret_keys():
    from services.handoff.audit import _safe_metadata

    md = {
        # content / secret / error-like keys — must be dropped even as scalars
        "subject": "s", "body": "b", "snippet": "sn", "clean_text": "c", "raw": "r",
        "mime": "m", "token": "t", "secret": "x", "credential": "cr", "password": "pw",
        "api_key": "k", "apikey": "k2", "exception": "e", "traceback": "tb",
        "prompt": "p", "response": "r2", "content": "c2",
        # safe scalar/count keys — must be kept
        "claims": 3, "evidence": 5, "excluded_sensitivity": 1, "reason": "vacation",
        "has_date_window": True,
    }
    out = _safe_metadata(md)
    assert out == {
        "claims": 3, "evidence": 5, "excluded_sensitivity": 1,
        "reason": "vacation", "has_date_window": True,
    }


# ── Migration presence + a representative constraint ─────────────────────────

@requires_db
def test_migration_tables_and_claim_cardinality_constraint(env):
    from sqlalchemy import exc, text as sqltext

    from services.db import models as orm

    s = _fresh()
    try:
        names = set(s.execute(sqltext(
            "SELECT tablename FROM pg_tables WHERE tablename LIKE 'handoff%'"
        )).scalars())
        assert {"handoff_package", "handoff_scope", "handoff_claim", "handoff_evidence",
                "handoff_exclusion", "handoff_audit_event"} <= names

        # Create a package to hang a claim off, then prove the cardinality CHECK.
        pkg = orm.HandoffPackage(mailbox_id=env.mid, creator_email=OWNER, reason="vacation",
                                 lineage_id=str(uuid.uuid4()))
        s.add(pkg)
        s.commit()
        s.add(orm.HandoffClaim(package_id=pkg.id, kind="decision", text="x",
                               source_message_id_headers=[]))  # empty → violates CHECK
        with pytest.raises(exc.IntegrityError):
            s.commit()
    finally:
        s.rollback()
        s.close()


# ── Create / scope ───────────────────────────────────────────────────────────

@requires_db
def test_create_draft_and_update_scope(env):
    r = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation", "title": "Cover me"})
    assert r.status_code == 200
    pkg = r.json()
    assert pkg["status"] == "draft" and pkg["reason"] == "vacation"
    pid = pkg["id"]

    r2 = env.client.patch(f"/api/handoff/{pid}/scope",
                          json={"date_from": "2026-04-01", "date_to": "2026-06-30"})
    assert r2.status_code == 200
    assert r2.json()["scope"]["date_from"] == "2026-04-01"

    # invalid reason and inverted window are rejected.
    assert env.client.post(f"/api/handoff/{env.mid}", json={"reason": "nope"}).status_code == 422
    assert env.client.patch(f"/api/handoff/{pid}/scope",
                            json={"date_from": "2026-06-30", "date_to": "2026-04-01"}).status_code == 422


# ── Generate from seeded L1 events ───────────────────────────────────────────

@requires_db
def test_generate_produces_cited_claims_and_evidence(env):
    tid = str(uuid.uuid4())
    _seed_thread(env.session, env.mid, tid, [
        {"header": "atlas-1@acme.corp", "subject": "Atlas cutover", "body": "Cutover Friday."},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["atlas-1@acme.corp"],
                type_="did", summary="Completed the Atlas cutover")

    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    r = env.client.post(f"/api/handoff/{pid}/generate")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "generated"
    assert len(body["claims"]) == 1
    claim = body["claims"][0]
    assert claim["kind"] == "decision" and claim["source_message_id_headers"] == ["atlas-1@acme.corp"]
    ev_headers = {e["message_id_header"] for e in body["evidence"]}
    assert ev_headers == {"atlas-1@acme.corp"}
    # every claim cites in-package evidence (invariant)
    assert all(set(c["source_message_id_headers"]) <= ev_headers for c in body["claims"])


@requires_db
def test_sensitive_whole_thread_excluded_from_evidence(env):
    # Clean thread → evidence eligible; mixed-sensitivity thread → excluded whole.
    clean_tid, sens_tid = str(uuid.uuid4()), str(uuid.uuid4())
    _seed_thread(env.session, env.mid, clean_tid, [
        {"header": "clean-1@acme.corp", "body": "safe content"},
    ])
    _seed_thread(env.session, env.mid, sens_tid, [
        {"header": "mix-clean@acme.corp", "body": "looks safe"},
        {"header": "mix-hr@acme.corp", "sensitivity": ["hr"], "body": "HR confidential"},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["clean-1@acme.corp"], summary="clean decision")
    # An event citing the *clean* sibling of a sensitive thread — still excluded.
    _seed_event(env.session, env.mid, env.owner_pid, ["mix-clean@acme.corp"], summary="tainted decision")

    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    body = env.client.post(f"/api/handoff/{pid}/generate").json()

    ev_headers = {e["message_id_header"] for e in body["evidence"]}
    assert "clean-1@acme.corp" in ev_headers
    assert "mix-clean@acme.corp" not in ev_headers  # whole-thread excluded
    assert "mix-hr@acme.corp" not in ev_headers
    # The tainted claim was dropped (no in-package evidence); only the clean one remains.
    assert len(body["claims"]) == 1
    assert body["exclusion_counts"].get("sensitivity", 0) >= 1

    # Belt-and-suspenders: no evidence row belongs to a sensitive thread.
    s = _fresh()
    try:
        from sqlalchemy import text as sqltext
        leaked = s.execute(sqltext(
            "SELECT count(*) FROM handoff_evidence he JOIN message m "
            "  ON m.message_id_header = he.message_id_header AND m.mailbox_id = :mid "
            "JOIN thread t ON t.id = m.thread_id "
            "WHERE he.package_id = :pid AND EXISTS ("
            "  SELECT 1 FROM message m2 WHERE m2.thread_id = t.id AND m2.sensitivity != '{none}')"
        ), {"mid": env.mid, "pid": pid}).scalar()
        assert leaked == 0
    finally:
        s.close()


@requires_db
def test_noise_message_excluded_from_evidence(env):
    tid = str(uuid.uuid4())
    _seed_thread(env.session, env.mid, tid, [
        {"header": "news-1@acme.corp", "subject": "Weekly digest", "body": "newsletter", "noise": True},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["news-1@acme.corp"], summary="from a newsletter")

    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    body = env.client.post(f"/api/handoff/{pid}/generate").json()

    assert body["evidence"] == []       # noise never becomes evidence
    assert body["claims"] == []          # its only citation was noise → claim dropped


@requires_db
def test_creator_exclusion_prevents_evidence(env):
    tid = str(uuid.uuid4())
    _seed_thread(env.session, env.mid, tid, [{"header": "rm-1@acme.corp", "body": "b"}])
    _seed_event(env.session, env.mid, env.owner_pid, ["rm-1@acme.corp"], summary="to be removed")

    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    # Creator removes the message before generating.
    env.client.patch(f"/api/handoff/{pid}/scope",
                     json={"excluded_message_id_headers": ["rm-1@acme.corp"]})
    body = env.client.post(f"/api/handoff/{pid}/generate").json()

    assert body["evidence"] == []
    assert body["claims"] == []  # its only citation was excluded → claim dropped
    assert body["exclusion_counts"].get("user_removed", 0) >= 1


# ── Audit safety ─────────────────────────────────────────────────────────────

@requires_db
def test_audit_metadata_has_no_content(env):
    tid = str(uuid.uuid4())
    _seed_thread(env.session, env.mid, tid, [
        {"header": "sec-1@acme.corp", "subject": "SECRET SUBJECT TOKEN",
         "body": "CONFIDENTIAL BODY SNIPPET"},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["sec-1@acme.corp"], summary="did work")

    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    env.client.post(f"/api/handoff/{pid}/generate")

    s = _fresh()
    try:
        from sqlalchemy import select
        from services.db import models as orm
        rows = s.execute(select(orm.HandoffAuditEvent).where(
            orm.HandoffAuditEvent.package_id == pid
        )).scalars().all()
        assert rows
        blob = " ".join(str(r.metadata_) for r in rows)
        for banned in ("SECRET SUBJECT TOKEN", "CONFIDENTIAL BODY SNIPPET"):
            assert banned not in blob
        # actions recorded
        actions = {r.action for r in rows}
        assert {"handoff_created", "candidate_generated"} <= actions
    finally:
        s.close()


# ── Illegal state transitions ────────────────────────────────────────────────

@requires_db
def test_illegal_transitions_rejected(env):
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    # Force a non-mutable status directly, then confirm scope/generate are 409.
    s = _fresh()
    try:
        from services.db import models as orm
        pkg = s.get(orm.HandoffPackage, pid)
        pkg.status = "published"
        s.commit()
    finally:
        s.close()

    assert env.client.patch(f"/api/handoff/{pid}/scope", json={}).status_code == 409
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 409
    # unknown ids → 404
    assert env.client.get(f"/api/handoff/{uuid.uuid4()}").status_code == 404
    assert env.client.get("/api/handoff/not-a-uuid").status_code == 404


# ── S17.5 — publish + recipient foundation ───────────────────────────────────

def _generated_package(env, header="atlas-1@acme.corp", summary="Completed the Atlas cutover"):
    """Seed one clean thread + event, then create+generate → returns package id."""
    tid = str(uuid.uuid4())
    _seed_thread(env.session, env.mid, tid, [
        {"header": header, "subject": "Atlas cutover", "body": "Cutover Friday."},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, [header], type_="did", summary=summary)
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 200
    return pid


def _publish(env, pid, recipient_email="cover@acme.corp", **body):
    return env.client.post(
        f"/api/handoff/{pid}/publish",
        json={"recipient_email": recipient_email, **body},
    )


def _exchange(env, code):
    return env.client.post("/api/handoff/recipient/session", json={"code": code})


@requires_db
def test_publish_creates_recipient_grant_and_default_expiry(env):
    from datetime import datetime as dt

    pid = _generated_package(env)
    r = _publish(env, pid)
    assert r.status_code == 200
    body = r.json()
    assert body["package"]["status"] == "published"
    assert body["package"]["published_at"] and body["package"]["expires_at"]
    assert body["recipient_email"] == "cover@acme.corp"
    # raw code returned once + share fragment (never a path/query).
    assert body["capability_code"]
    assert body["share_fragment"] == f"#c={body['capability_code']}"

    published = dt.fromisoformat(body["package"]["published_at"])
    expires = dt.fromisoformat(body["expires_at"])
    assert 29 <= (expires - published).days <= 30  # default +30d

    # A recipient grant exists; only the HASH of the code is stored.
    s = _fresh()
    try:
        from sqlalchemy import select

        from services.db import models as orm
        from services.handoff.tokens import hash_token
        rec = s.execute(select(orm.HandoffRecipient).where(
            orm.HandoffRecipient.package_id == pid)).scalar_one()
        assert rec.recipient_email == "cover@acme.corp"
        assert rec.capability_code_hash == hash_token(body["capability_code"])
        assert body["capability_code"] not in rec.capability_code_hash
    finally:
        s.close()


@requires_db
def test_publish_expiry_override_honored(env):
    from datetime import datetime as dt

    pid = _generated_package(env)
    body = _publish(env, pid, expires_in_days=7).json()
    published = dt.fromisoformat(body["package"]["published_at"])
    expires = dt.fromisoformat(body["expires_at"])
    assert 6 <= (expires - published).days <= 7


@requires_db
def test_cannot_publish_empty_or_ungenerated_package(env):
    # Generated but with no events → no evidence → publish blocked (nothing to read).
    empty_pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert env.client.post(f"/api/handoff/{empty_pid}/generate").json()["evidence"] == []
    assert _publish(env, empty_pid).status_code == 409

    # A draft that was never generated cannot be published either.
    draft_pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert _publish(env, draft_pid).status_code == 409


@requires_db
def test_published_package_is_immutable(env):
    pid = _generated_package(env)
    assert _publish(env, pid).status_code == 200
    # Scope edit and regenerate are both rejected once published.
    assert env.client.patch(f"/api/handoff/{pid}/scope", json={}).status_code == 409
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 409


@requires_db
def test_session_exchange_valid_and_invalid(env):
    pid = _generated_package(env)
    code = _publish(env, pid).json()["capability_code"]

    ok = _exchange(env, code)
    assert ok.status_code == 200
    sess = ok.json()
    assert sess["session_token"] and sess["package_id"] == pid

    # Wrong code → neutral unavailable, no session issued.
    bad = _exchange(env, "not-a-real-code")
    assert bad.status_code == 403

    # Only the session-token HASH is stored, never the raw bearer.
    s = _fresh()
    try:
        from sqlalchemy import select

        from services.db import models as orm
        from services.handoff.tokens import hash_token
        row = s.execute(select(orm.HandoffRecipientSession).where(
            orm.HandoffRecipientSession.package_id == pid)).scalar_one()
        assert row.session_token_hash == hash_token(sess["session_token"])
        assert sess["session_token"] not in row.session_token_hash
    finally:
        s.close()


@requires_db
def test_capability_code_is_one_time(env):
    # S17.2 §7.1: the capability code is consumed on exchange. The first exchange
    # succeeds; replaying the same raw code returns the neutral unavailable
    # response and issues no second session.
    pid = _generated_package(env)
    code = _publish(env, pid).json()["capability_code"]

    first = _exchange(env, code)
    assert first.status_code == 200
    first_token = first.json()["session_token"]

    # Same code again → neutral 403, byte-identical to an invalid code (no oracle
    # that this code once existed / was already spent).
    second = _exchange(env, code)
    assert second.status_code == 403
    assert second.json() == _exchange(env, "not-a-real-code").json()

    s = _fresh()
    try:
        from sqlalchemy import func as sqlfunc, select

        from services.db import models as orm
        from services.handoff.tokens import hash_token
        rec = s.execute(select(orm.HandoffRecipient).where(
            orm.HandoffRecipient.package_id == pid)).scalar_one()
        # DB records the code as consumed after the first exchange.
        assert rec.capability_code_consumed_at is not None
        # Still only the code HASH is stored — never the raw code.
        assert rec.capability_code_hash == hash_token(code)
        # Exactly ONE session was ever issued; the blocked replay created none.
        n_sessions = s.execute(select(sqlfunc.count()).select_from(
            orm.HandoffRecipientSession).where(
            orm.HandoffRecipientSession.package_id == pid)).scalar_one()
        assert n_sessions == 1
    finally:
        s.close()

    # The one session issued on the first exchange still works — consuming the
    # code does not invalidate an already-granted session.
    assert env.client.get("/api/handoff/recipient/package",
                          headers={"Authorization": f"Bearer {first_token}"}).status_code == 200


@requires_db
def test_session_exchange_blocked_when_expired(env):
    from datetime import datetime as dt, timedelta, timezone as tz

    pid = _generated_package(env)
    code = _publish(env, pid).json()["capability_code"]

    # Force expiry into the past while status stays 'published' → exchange blocked
    # at request time (does not depend on a status-flip job).
    s = _fresh()
    try:
        from services.db import models as orm
        pkg = s.get(orm.HandoffPackage, pid)
        pkg.expires_at = dt.now(tz.utc) - timedelta(days=1)
        s.commit()
    finally:
        s.close()
    assert _exchange(env, code).status_code == 403


@requires_db
def test_session_exchange_blocked_when_revoked(env):
    pid = _generated_package(env)
    code = _publish(env, pid).json()["capability_code"]
    # Revoke a live published package, then the code can no longer be exchanged.
    assert env.client.post(f"/api/handoff/{pid}/revoke").status_code == 200
    assert _exchange(env, code).status_code == 403


@requires_db
def test_recipient_package_is_local_and_omits_counts_and_links(env):
    pid = _generated_package(env)
    code = _publish(env, pid).json()["capability_code"]
    token = _exchange(env, code).json()["session_token"]

    r = env.client.get("/api/handoff/recipient/package",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    # Claims + evidence come straight from the package tables.
    assert len(body["claims"]) == 1
    assert {e["message_id_header"] for e in body["evidence"]} == {"atlas-1@acme.corp"}
    # Global posture present; NO counts, NO Gmail/source link, NO mailbox id.
    assert body["privacy_posture"]["sensitive_excluded"] is True
    assert "exclusion_counts" not in body
    assert "mailbox_id" not in body
    for e in body["evidence"]:
        assert "open_url" not in e
        assert "gmail" not in json_dumps(e).lower()

    # No bearer → neutral unavailable.
    assert env.client.get("/api/handoff/recipient/package").status_code == 403


@requires_db
def test_recipient_blocked_after_expiry_even_if_status_published(env):
    from datetime import datetime as dt, timedelta, timezone as tz

    pid = _generated_package(env)
    code = _publish(env, pid).json()["capability_code"]
    token = _exchange(env, code).json()["session_token"]
    # Session is live now; GET works.
    assert env.client.get("/api/handoff/recipient/package",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 200

    # Expire the package at the DB level but keep status 'published' (as if the
    # status-flip job never ran). Access must still be blocked at request time.
    s = _fresh()
    try:
        from services.db import models as orm
        pkg = s.get(orm.HandoffPackage, pid)
        pkg.expires_at = dt.now(tz.utc) - timedelta(minutes=1)
        assert pkg.status == "published"
        s.commit()
    finally:
        s.close()
    assert env.client.get("/api/handoff/recipient/package",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 403


@requires_db
def test_revoke_blocks_recipient_access(env):
    pid = _generated_package(env)
    code = _publish(env, pid).json()["capability_code"]
    token = _exchange(env, code).json()["session_token"]
    assert env.client.get("/api/handoff/recipient/package",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 200

    assert env.client.post(f"/api/handoff/{pid}/revoke").status_code == 200
    # Existing session is dead and the code can no longer be exchanged.
    assert env.client.get("/api/handoff/recipient/package",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert _exchange(env, code).status_code == 403


@requires_db
def test_recipient_audit_is_safe_and_actor_is_hashed(env):
    # Seed content-bearing message so we can prove no body/subject leaks into audit.
    tid = str(uuid.uuid4())
    _seed_thread(env.session, env.mid, tid, [
        {"header": "rcpt-1@acme.corp", "subject": "SECRET RECIPIENT SUBJECT",
         "body": "CONFIDENTIAL RECIPIENT BODY"},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["rcpt-1@acme.corp"], summary="did work")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    env.client.post(f"/api/handoff/{pid}/generate")

    code = _publish(env, pid).json()["capability_code"]
    token = _exchange(env, code).json()["session_token"]
    env.client.get("/api/handoff/recipient/package",
                   headers={"Authorization": f"Bearer {token}"})

    s = _fresh()
    try:
        from sqlalchemy import select

        from services.db import models as orm
        rows = s.execute(select(orm.HandoffAuditEvent).where(
            orm.HandoffAuditEvent.package_id == pid)).scalars().all()
        actions = {r.action for r in rows}
        assert {"recipient_session_started", "recipient_viewed"} <= actions
        # Recipient actor is a hash prefix, never a raw code/token.
        rcpt_actors = [r.actor for r in rows if r.actor.startswith("recipient:")]
        assert rcpt_actors and all(code not in a and token not in a for a in rcpt_actors)
        # No content, no raw secret anywhere in audit metadata.
        blob = " ".join(str(r.metadata_) for r in rows)
        for banned in ("SECRET RECIPIENT SUBJECT", "CONFIDENTIAL RECIPIENT BODY", code, token):
            assert banned not in blob
    finally:
        s.close()


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj)


# ── S17.8 end-to-end journey ─────────────────────────────────────────────────

@requires_db
def test_full_creator_to_recipient_journey(env):
    """One contiguous walk of the documented creator→recipient→revoke flow
    (steps 2–16 of docs/s17-live-validation.md). Proves the state machine, the
    evidence-removal → claim-drop behavior, publish + default expiry, creator
    immutability, the one-time capability code, recipient session reuse, and
    revoke — all in a single sequence. Raw code/token values are asserted on but
    NEVER printed."""
    from datetime import datetime as dt

    # Two independent events, each citing its own message, so removing one
    # evidence item can be shown to drop exactly the claim that depended on it.
    tid = str(uuid.uuid4())
    _seed_thread(env.session, env.mid, tid, [
        {"header": "atlas-a@acme.corp", "subject": "Atlas cutover", "body": "Cutover Friday."},
        {"header": "atlas-b@acme.corp", "subject": "Billing owner", "body": "Dana owns billing."},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["atlas-a@acme.corp"],
                summary="Completed the Atlas cutover")
    _seed_event(env.session, env.mid, env.owner_pid, ["atlas-b@acme.corp"],
                summary="Handed billing ownership to Dana")

    # (2) create draft
    pkg = env.client.post(f"/api/handoff/{env.mid}",
                          json={"reason": "vacation", "title": "Cover Atlas"}).json()
    pid = pkg["id"]
    assert pkg["status"] == "draft"

    # (3) optional date scope
    scoped = env.client.patch(f"/api/handoff/{pid}/scope",
                              json={"date_from": "2026-01-01", "date_to": "2026-12-31"})
    assert scoped.status_code == 200 and scoped.json()["status"] == "draft"

    # (4) generate → claims + evidence for both messages
    gen = env.client.post(f"/api/handoff/{pid}/generate").json()
    assert gen["status"] == "generated"
    assert {e["message_id_header"] for e in gen["evidence"]} == {
        "atlas-a@acme.corp", "atlas-b@acme.corp"}
    claims_before = len(gen["claims"])
    assert claims_before >= 2

    # (5) creator-only exclusion summary present on the creator view
    assert "exclusion_counts" in gen

    # (6) remove one evidence item + regenerate → its unsupported claim disappears
    full_scope = {
        "date_from": "2026-01-01", "date_to": "2026-12-31",
        "included_project_ids": [], "included_person_ids": [], "included_thread_ids": [],
        "excluded_thread_ids": [], "excluded_message_id_headers": ["atlas-a@acme.corp"],
        "allowed_domains": [], "keyword_filters": [],
    }
    assert env.client.patch(f"/api/handoff/{pid}/scope", json=full_scope).status_code == 200
    regen = env.client.post(f"/api/handoff/{pid}/generate").json()
    assert {e["message_id_header"] for e in regen["evidence"]} == {"atlas-b@acme.corp"}
    for c in regen["claims"]:  # no dangling claim survives without in-package evidence
        assert set(c["source_message_id_headers"]) <= {"atlas-b@acme.corp"}
    assert len(regen["claims"]) < claims_before

    # (7) publish with one recipient + default 30-day expiry
    pub = env.client.post(f"/api/handoff/{pid}/publish",
                          json={"recipient_email": "cover@acme.corp"})
    assert pub.status_code == 200
    pub = pub.json()
    code = pub["capability_code"]  # asserted on below; never printed
    assert pub["package"]["status"] == "published"
    assert pub["share_fragment"] == f"#c={code}"
    published = dt.fromisoformat(pub["package"]["published_at"])
    expires = dt.fromisoformat(pub["expires_at"])
    assert 29 <= (expires - published).days <= 30

    # (9) creator refresh: raw code is NOT recoverable from the creator GET, and
    # the package is immutable (scope + regenerate both rejected).
    refetched = env.client.get(f"/api/handoff/{pid}").json()
    assert refetched["status"] == "published"
    assert "capability_code" not in refetched and "share_fragment" not in refetched
    assert env.client.patch(f"/api/handoff/{pid}/scope", json=full_scope).status_code == 409
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 409

    # (10–12) recipient exchanges the one-time code and reads the package-local
    # view — no mailbox id, no exclusion counts, no source/Gmail link.
    sess = env.client.post("/api/handoff/recipient/session", json={"code": code})
    assert sess.status_code == 200
    token = sess.json()["session_token"]  # asserted on below; never printed
    auth = {"Authorization": f"Bearer {token}"}
    view = env.client.get("/api/handoff/recipient/package", headers=auth)
    assert view.status_code == 200
    rv = view.json()
    assert rv["title"] == "Cover Atlas" and rv["creator_email"] == OWNER
    assert rv["reason"] == "vacation" and rv["published_at"] and rv["expires_at"]
    assert rv["privacy_posture"]["sensitive_excluded"] is True
    assert {e["message_id_header"] for e in rv["evidence"]} == {"atlas-b@acme.corp"}
    blob = json_dumps(rv).lower()
    for banned in ("mailbox_id", "exclusion_counts", "open_url", "gmail"):
        assert banned not in blob

    # (13) recipient refresh (same stored session token) still renders
    assert env.client.get("/api/handoff/recipient/package", headers=auth).status_code == 200

    # (14) the consumed one-time code cannot mint a second session
    assert env.client.post("/api/handoff/recipient/session", json={"code": code}).status_code == 403

    # (15) creator revokes
    assert env.client.post(f"/api/handoff/{pid}/revoke").status_code == 200

    # (16) the previously-live recipient session is now blocked (neutral 403)
    assert env.client.get("/api/handoff/recipient/package", headers=auth).status_code == 403


# ── S17.9 — recipient package-local ask ──────────────────────────────────────

def _ask(env, token, query):
    return env.client.post(
        "/api/handoff/recipient/ask",
        json={"query": query},
        headers={"Authorization": f"Bearer {token}"},
    )


def _published_session(env, **seed):
    """Publish _generated_package and return (pid, session_token)."""
    pid = _generated_package(env, **seed)
    code = _publish(env, pid).json()["capability_code"]
    token = _exchange(env, code).json()["session_token"]
    return pid, token


def test_answer_from_package_grounding_is_pure_and_evidence_gated():
    """DB-free: no package evidence -> no answer; empty query -> no answer."""
    from types import SimpleNamespace

    from services.handoff.ask import answer_from_package

    ev = SimpleNamespace(
        message_id_header="atlas-1@acme.corp", subject="Atlas cutover",
        body_snapshot="Cutover is Friday.", sender_display="Al", sender_domain="acme.corp",
        ts=None,
    )
    claim = SimpleNamespace(kind="decision", text="Atlas cutover completed",
                            source_message_id_headers=["atlas-1@acme.corp"])

    hit = answer_from_package("atlas cutover", [claim], [ev])
    assert hit.answered is True
    assert [e.message_id_header for e in hit.evidence] == ["atlas-1@acme.corp"]

    # No overlap -> not answered, nothing cited.
    miss = answer_from_package("payroll vacation policy", [claim], [ev])
    assert miss.answered is False and miss.evidence == [] and miss.claims == []

    # Empty/stopword-only query -> not answered.
    assert answer_from_package("what is the", [claim], [ev]).answered is False
    # Evidence present but claims empty still answers from evidence overlap.
    assert answer_from_package("atlas", [], [ev]).answered is True


@requires_db
def test_recipient_ask_answers_and_cites_only_in_package_evidence(env):
    _pid, token = _published_session(env)
    r = _ask(env, token, "What is the status of the Atlas cutover?")
    assert r.status_code == 200
    body = r.json()
    assert body["answered"] is True
    # Every cited evidence header is an in-package header.
    ev_headers = {e["message_id_header"] for e in body["evidence"]}
    assert ev_headers == {"atlas-1@acme.corp"}
    # Every claim cites only in-package headers, and no source/Gmail link leaks.
    for c in body["claims"]:
        assert set(c["source_message_id_headers"]) <= ev_headers
    blob = json_dumps(body).lower()
    for banned in ("mailbox_id", "exclusion_counts", "open_url", "gmail"):
        assert banned not in blob


@requires_db
def test_recipient_ask_no_match_is_neutral_no_evidence(env):
    _pid, token = _published_session(env)
    r = _ask(env, token, "quarterly payroll reimbursement policy")
    assert r.status_code == 200
    body = r.json()
    assert body["answered"] is False
    assert body["claims"] == [] and body["evidence"] == []
    assert "doesn't include anything" in body["message"]


@requires_db
def test_recipient_ask_excluded_topic_is_identical_neutral_no_oracle(env):
    """A query about content that was sensitivity-excluded from the package must
    return a response byte-identical to a query about content that never existed
    — so the ask cannot reveal that sensitive content was withheld."""
    # Clean event (in package) + a sensitive thread (excluded whole).
    clean_tid, sens_tid = str(uuid.uuid4()), str(uuid.uuid4())
    _seed_thread(env.session, env.mid, clean_tid, [
        {"header": "clean-1@acme.corp", "subject": "Atlas cutover", "body": "Cutover Friday."},
    ])
    _seed_thread(env.session, env.mid, sens_tid, [
        {"header": "horizon-hr@acme.corp", "sensitivity": ["hr"],
         "subject": "Horizon layoffs", "body": "Confidential Horizon layoff plan."},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["clean-1@acme.corp"], summary="Atlas cutover done")
    _seed_event(env.session, env.mid, env.owner_pid, ["horizon-hr@acme.corp"], summary="Horizon layoff plan")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    env.client.post(f"/api/handoff/{pid}/generate")
    code = _publish(env, pid).json()["capability_code"]
    token = _exchange(env, code).json()["session_token"]

    # Ask about the EXCLUDED sensitive topic vs a topic that never existed.
    excluded = _ask(env, token, "What is the Horizon layoff plan?").json()
    unknown = _ask(env, token, "What is the Zephyr merger plan?").json()
    assert excluded["answered"] is False
    assert excluded == unknown  # byte-identical: no existence oracle


@requires_db
def test_recipient_ask_blocked_when_revoked_or_expired(env):
    from datetime import datetime as dt, timedelta, timezone as tz

    pid, token = _published_session(env)
    assert _ask(env, token, "atlas").status_code == 200  # live now

    assert env.client.post(f"/api/handoff/{pid}/revoke").status_code == 200
    assert _ask(env, token, "atlas").status_code == 403  # revoked → cannot ask

    # Fresh package, force expiry into the past → ask blocked at request time.
    pid2, token2 = _published_session(env, header="b-1@acme.corp", summary="Billing handed to Dana")
    s = _fresh()
    try:
        from services.db import models as orm
        pkg = s.get(orm.HandoffPackage, pid2)
        pkg.expires_at = dt.now(tz.utc) - timedelta(days=1)
        s.commit()
    finally:
        s.close()
    assert _ask(env, token2, "billing").status_code == 403


@requires_db
def test_recipient_ask_is_package_local_after_live_tables_wiped(env):
    """Proves the ask path reads only the package snapshot: delete every
    Message/Thread/Event row for the mailbox after publish, then ask — it still
    answers from handoff_evidence alone."""
    _pid, token = _published_session(env)
    s = _fresh()
    try:
        from services.db import models as orm
        for model in (orm.Event, orm.Message, orm.Thread):
            s.execute(model.__table__.delete().where(model.mailbox_id == env.mid))
        s.commit()
    finally:
        s.close()
    r = _ask(env, token, "Atlas cutover")
    assert r.status_code == 200
    body = r.json()
    assert body["answered"] is True
    assert {e["message_id_header"] for e in body["evidence"]} == {"atlas-1@acme.corp"}


@requires_db
def test_recipient_ask_audit_is_safe(env):
    _pid, token = _published_session(env)
    secret_query = "SECRET horizon layoff CONFIDENTIAL atlas"
    assert _ask(env, token, secret_query).status_code == 200

    s = _fresh()
    try:
        from sqlalchemy import select

        from services.db import models as orm
        rows = s.execute(select(orm.HandoffAuditEvent).where(
            orm.HandoffAuditEvent.package_id == _pid,
            orm.HandoffAuditEvent.action == "package_asked",
        )).scalars().all()
        assert rows, "expected a package_asked audit row"
        meta_keys = set()
        blob = ""
        for r in rows:
            meta_keys |= set((r.metadata_ or {}).keys())
            blob += " " + str(r.metadata_)
        # Only safe, non-content keys; the raw query text never appears.
        assert meta_keys <= {"query_len", "matched_evidence"}
        assert "SECRET" not in blob and "horizon" not in blob and "CONFIDENTIAL" not in blob
        assert secret_query not in blob
        # Actor is a hash prefix, never the raw token.
        actors = [r.actor for r in rows]
        assert all(a.startswith("recipient:") for a in actors)
        assert all(token not in a for a in actors)
    finally:
        s.close()


def test_ask_claims_never_cite_evidence_outside_returned_set():
    """P1 regression (DB-free): when more than _MAX_EVIDENCE direct matches fill
    the evidence cap, a claim citing an in-package row that gets capped OUT must
    not appear with a dangling citation. Every returned claim must cite only rows
    present in the returned evidence."""
    from types import SimpleNamespace

    from services.handoff.ask import _MAX_EVIDENCE, answer_from_package

    # More direct-match evidence rows than the cap — all match "atlas" directly.
    evidence = [
        SimpleNamespace(
            message_id_header=f"atlas-{i}@x", subject="Atlas cutover",
            body_snapshot="atlas rollout work", sender_display="", sender_domain="", ts=None,
        )
        for i in range(_MAX_EVIDENCE + 2)
    ]
    # A separate in-package row that does NOT match the query directly, but is
    # cited by a claim whose TEXT does match — so it is appended after the direct
    # matches and pushed out by the cap.
    evidence.append(SimpleNamespace(
        message_id_header="budget-1@x", subject="Quarterly budget",
        body_snapshot="budget review", sender_display="", sender_domain="", ts=None,
    ))
    claim_capped = SimpleNamespace(
        kind="decision", text="Atlas migration budget", source_message_id_headers=["budget-1@x"],
    )
    # A second claim that cites BOTH a returned row and the capped-out row.
    claim_mixed = SimpleNamespace(
        kind="decision", text="Atlas rollout owner",
        source_message_id_headers=["atlas-0@x", "budget-1@x"],
    )

    res = answer_from_package("atlas", [claim_capped, claim_mixed], evidence)
    assert res.answered is True
    returned = {e.message_id_header for e in res.evidence}
    assert len(returned) == _MAX_EVIDENCE and "budget-1@x" not in returned

    # Module contract: every returned claim has >=1 citation in the returned set;
    # the budget-only claim is dropped entirely.
    assert all(any(h in returned for h in c.source_message_id_headers) for c in res.claims)
    assert claim_capped not in res.claims

    # Endpoint contract: RecipientAskResponse claims cite ONLY returned evidence.
    # Reproduce the endpoint's per-claim header filter + drop-empty.
    answer_claims = [
        [h for h in c.source_message_id_headers if h in returned] for c in res.claims
    ]
    answer_claims = [hs for hs in answer_claims if hs]
    for hs in answer_claims:
        assert set(hs) <= returned  # no citation outside the returned evidence


# ── S17.10 — package versioning + new-version re-share ───────────────────────

def _new_version(env, pid):
    return env.client.post(f"/api/handoff/{pid}/new-version")


@requires_db
def test_new_version_from_published_creates_draft_bumped_version_same_lineage(env):
    pid = _generated_package(env)
    _publish(env, pid)
    r = _new_version(env, pid)
    assert r.status_code == 200
    new = r.json()
    assert new["status"] == "draft" and new["version"] == 2 and new["id"] != pid
    # No recipient/session/code/published metadata carried over.
    assert new["published_at"] is None and new["expires_at"] is None and new["revoked_at"] is None
    assert new["claims"] == [] and new["evidence"] == []

    s = _fresh()
    try:
        from sqlalchemy import select

        from services.db import models as orm
        old = s.get(orm.HandoffPackage, pid)
        newp = s.get(orm.HandoffPackage, new["id"])
        assert newp.lineage_id == old.lineage_id
        assert newp.supersedes_package_id == pid
        assert newp.version == old.version + 1
        # No recipient row cloned onto the new draft.
        assert s.execute(select(orm.HandoffRecipient).where(
            orm.HandoffRecipient.package_id == newp.id)).scalar_one_or_none() is None
    finally:
        s.close()


@requires_db
def test_new_version_copies_previous_scope(env):
    pid = _generated_package(env)
    scope = {
        "date_from": "2026-01-01", "date_to": "2026-12-31",
        "included_project_ids": [], "included_person_ids": [], "included_thread_ids": [],
        "excluded_thread_ids": [], "excluded_message_id_headers": ["ghost@acme.corp"],
        "allowed_domains": ["acme.corp"], "keyword_filters": ["atlas"],
    }
    assert env.client.patch(f"/api/handoff/{pid}/scope", json=scope).status_code == 200
    env.client.post(f"/api/handoff/{pid}/generate")
    _publish(env, pid)

    ns = _new_version(env, pid).json()["scope"]
    assert ns["date_from"] == "2026-01-01" and ns["date_to"] == "2026-12-31"
    assert ns["excluded_message_id_headers"] == ["ghost@acme.corp"]
    assert ns["allowed_domains"] == ["acme.corp"]
    assert ns["keyword_filters"] == ["atlas"]


@requires_db
def test_new_version_can_generate_and_publish_with_fresh_code(env):
    pid = _generated_package(env)
    code1 = _publish(env, pid).json()["capability_code"]
    new = _new_version(env, pid).json()
    # Fresh draft re-snapshots evidence on generate, then publishes normally.
    assert env.client.post(f"/api/handoff/{new['id']}/generate").json()["status"] == "generated"
    pub2 = _publish(env, new["id"])
    assert pub2.status_code == 200
    code2 = pub2.json()["capability_code"]
    assert code2 and code2 != code1  # a fresh one-time code
    # The new recipient can open the new version.
    assert _exchange(env, code2).status_code == 200


@requires_db
def test_publishing_new_version_supersedes_prior_and_blocks_old_recipient(env):
    pid = _generated_package(env)
    code1 = _publish(env, pid).json()["capability_code"]
    token1 = _exchange(env, code1).json()["session_token"]
    auth1 = {"Authorization": f"Bearer {token1}"}
    assert env.client.get("/api/handoff/recipient/package", headers=auth1).status_code == 200

    new = _new_version(env, pid).json()
    env.client.post(f"/api/handoff/{new['id']}/generate")
    assert _publish(env, new["id"]).status_code == 200

    # Prior package is now superseded, and the old session/package is blocked.
    assert env.client.get(f"/api/handoff/{pid}").json()["status"] == "superseded"
    assert env.client.get("/api/handoff/recipient/package", headers=auth1).status_code == 403


@requires_db
def test_old_unexchanged_code_cannot_open_after_supersede(env):
    pid = _generated_package(env)
    code1 = _publish(env, pid).json()["capability_code"]  # never exchanged
    new = _new_version(env, pid).json()
    env.client.post(f"/api/handoff/{new['id']}/generate")
    assert _publish(env, new["id"]).status_code == 200
    # v1 is superseded → its still-unused one-time code cannot mint a session.
    assert _exchange(env, code1).status_code == 403


@requires_db
def test_revoked_package_can_create_new_version(env):
    pid = _generated_package(env)
    _publish(env, pid)
    assert env.client.post(f"/api/handoff/{pid}/revoke").status_code == 200
    r = _new_version(env, pid)
    assert r.status_code == 200 and r.json()["version"] == 2


@requires_db
def test_draft_or_generated_cannot_create_new_version(env):
    # Draft (never generated) cannot fork.
    draft = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert _new_version(env, draft).status_code == 409
    # Generated-but-unpublished cannot fork either.
    gen = _generated_package(env)
    assert _new_version(env, gen).status_code == 409


@requires_db
def test_published_original_remains_immutable_across_versioning(env):
    pid = _generated_package(env)
    _publish(env, pid)
    _new_version(env, pid)  # forking must not mutate the original
    assert env.client.patch(f"/api/handoff/{pid}/scope", json={}).status_code == 409
    assert env.client.post(f"/api/handoff/{pid}/generate").status_code == 409
    assert env.client.get(f"/api/handoff/{pid}").json()["status"] == "published"


@requires_db
def test_versioning_audit_is_safe(env):
    pid = _generated_package(env)
    _publish(env, pid)
    new = _new_version(env, pid).json()
    env.client.post(f"/api/handoff/{new['id']}/generate")
    _publish(env, new["id"])  # triggers supersede of v1

    s = _fresh()
    try:
        from sqlalchemy import select

        from services.db import models as orm
        lineage = s.get(orm.HandoffPackage, new["id"]).lineage_id
        rows = s.execute(select(orm.HandoffAuditEvent).where(
            orm.HandoffAuditEvent.lineage_id == lineage)).scalars().all()
        by_action = {}
        for r in rows:
            by_action.setdefault(r.action, []).append(r)
        assert "package_version_created" in by_action and "package_superseded" in by_action

        # The versioning rows carry ONLY the specified safe id/version keys.
        version_keys = {"old_package_id", "new_package_id", "lineage_id",
                        "old_version", "new_version"}
        for action in ("package_version_created", "package_superseded"):
            for r in by_action[action]:
                assert set((r.metadata_ or {}).keys()) == version_keys

        # No audit row anywhere in the lineage leaks content or a secret: neither a
        # content/secret-like KEY nor known message content ("Cutover" body /
        # "Atlas cutover" subject) in any value.
        _banned_frag = ("body", "subject", "snippet", "token", "secret",
                        "capability", "code", "content", "clean_text")
        for r in rows:
            for k in (r.metadata_ or {}):
                assert not any(f in k.lower() for f in _banned_frag)
            blob = str(r.metadata_)
            assert "Cutover" not in blob and "Atlas" not in blob
    finally:
        s.close()


# ── S17.11 — static HTML export ──────────────────────────────────────────────

def _export(env, pid):
    return env.client.get(f"/api/handoff/{pid}/export.html")


@requires_db
def test_export_published_returns_html_with_metadata_claims_evidence(env):
    pid = _generated_package(env)
    _publish(env, pid)
    r = _export(env, pid)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "charset=utf-8" in r.headers["content-type"].lower()
    assert 'attachment; filename="handoff-package-v1.html"' in r.headers.get("content-disposition", "")
    body = r.text
    # Package metadata + posture language shared with the recipient view.
    assert "Atlas cutover" in body  # evidence subject
    assert "Cutover Friday." in body  # evidence body snapshot
    assert "Completed the Atlas cutover" in body  # claim text
    assert OWNER in body  # creator email
    assert "Sensitive and out-of-scope content has been excluded" in body


@requires_db
def test_export_draft_or_generated_rejected(env):
    draft = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    assert _export(env, draft).status_code == 409
    gen = _generated_package(env)
    assert _export(env, gen).status_code == 409


@requires_db
def test_export_revoked_and_superseded_are_marked(env):
    # Revoked
    pid = _generated_package(env)
    _publish(env, pid)
    env.client.post(f"/api/handoff/{pid}/revoke")
    rv = _export(env, pid)
    assert rv.status_code == 200 and "Revoked" in rv.text

    # Superseded: publish a new version over a prior published one.
    pid2 = _generated_package(env, header="s-1@acme.corp", summary="Superseded work")
    _publish(env, pid2)
    new = env.client.post(f"/api/handoff/{pid2}/new-version").json()
    env.client.post(f"/api/handoff/{new['id']}/generate")
    _publish(env, new["id"])
    sup = _export(env, pid2)
    assert sup.status_code == 200
    assert "Superseded" in sup.text and "replaced by a newer" in sup.text


@requires_db
def test_export_omits_secrets_and_recipient_only_fields(env):
    pid = _generated_package(env)
    code = _publish(env, pid).json()["capability_code"]
    body = _export(env, pid).text
    # No raw capability code, no mailbox id, no exclusion counts, no source links.
    assert code not in body
    assert env.mid not in body
    for banned in ("exclusion_count", "open_url", "gmail", "mailbox_id", "capability_code", "session_token"):
        assert banned not in body.lower()


@requires_db
def test_export_escapes_hostile_text(env):
    # Seed a package whose subject/body/claim carry an injection attempt.
    tid = str(uuid.uuid4())
    xss = "<script>alert(1)</script>"
    _seed_thread(env.session, env.mid, tid, [
        {"header": "xss-1@acme.corp", "subject": f"Danger {xss}", "body": f"Body {xss}"},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["xss-1@acme.corp"],
                summary=f"Claim {xss}")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    env.client.post(f"/api/handoff/{pid}/generate")
    _publish(env, pid)

    body = _export(env, pid).text
    # The raw executable tag must NOT appear; the escaped form must.
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


@requires_db
def test_export_is_package_local_after_live_tables_wiped(env):
    pid = _generated_package(env)
    _publish(env, pid)
    s = _fresh()
    try:
        from services.db import models as orm
        for model in (orm.Event, orm.Message, orm.Thread):
            s.execute(model.__table__.delete().where(model.mailbox_id == env.mid))
        s.commit()
    finally:
        s.close()
    r = _export(env, pid)
    assert r.status_code == 200
    assert "Atlas cutover" in r.text  # served purely from the snapshot


@requires_db
def test_export_audit_is_safe(env):
    pid = _generated_package(env)
    _publish(env, pid)
    _export(env, pid)
    s = _fresh()
    try:
        from sqlalchemy import select

        from services.db import models as orm
        rows = s.execute(select(orm.HandoffAuditEvent).where(
            orm.HandoffAuditEvent.package_id == pid,
            orm.HandoffAuditEvent.action == "package_exported",
        )).scalars().all()
        assert rows, "expected a package_exported audit row"
        for r in rows:
            assert set((r.metadata_ or {}).keys()) == {"version", "status", "claims", "evidence"}
            blob = str(r.metadata_)
            assert "Cutover" not in blob and "Atlas" not in blob
    finally:
        s.close()


# ── S17.13 — empty-generation diagnostic (creator-only) ──────────────────────

@requires_db
def test_generation_diagnostic_no_events_for_mailbox(env):
    # Mailbox has ZERO Event rows (the puluo situation): generate → empty package,
    # diagnostic says widening the date range will not help.
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    body = env.client.post(f"/api/handoff/{pid}/generate").json()
    assert body["status"] == "generated"
    assert body["claims"] == [] and body["evidence"] == []
    assert body["generation"] == {"code": "no_events_for_mailbox", "event_count": 0}
    # It survives a reload — GET recomputes the diagnostic.
    assert env.client.get(f"/api/handoff/{pid}").json()["generation"]["code"] == "no_events_for_mailbox"


@requires_db
def test_generation_diagnostic_no_events_in_scope(env):
    tid = str(uuid.uuid4())
    _seed_thread(env.session, env.mid, tid, [
        {"header": "atlas-1@acme.corp", "subject": "Atlas cutover", "body": "Cutover Friday."},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["atlas-1@acme.corp"], summary="Atlas cutover")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    # Date window that excludes the 2026-04 event entirely.
    env.client.patch(f"/api/handoff/{pid}/scope",
                     json={"date_from": "2020-01-01", "date_to": "2020-12-31"})
    body = env.client.post(f"/api/handoff/{pid}/generate").json()
    assert body["claims"] == [] and body["evidence"] == []
    assert body["generation"]["code"] == "no_events_in_scope"
    assert body["generation"]["event_count"] >= 1


@requires_db
def test_generation_diagnostic_all_events_excluded_by_policy(env):
    # Event cites a message in a whole-thread-sensitive thread → in the scope
    # window but gated out by the sensitivity policy.
    tid = str(uuid.uuid4())
    _seed_thread(env.session, env.mid, tid, [
        {"header": "hr-1@acme.corp", "sensitivity": ["hr"], "subject": "HR", "body": "confidential"},
    ])
    _seed_event(env.session, env.mid, env.owner_pid, ["hr-1@acme.corp"], summary="HR decision")
    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation"}).json()["id"]
    body = env.client.post(f"/api/handoff/{pid}/generate").json()
    assert body["claims"] == [] and body["evidence"] == []
    assert body["generation"]["code"] == "all_events_excluded_by_policy"
    assert body["generation"]["event_count"] >= 1


@requires_db
def test_generation_diagnostic_absent_when_package_has_content(env):
    pid = _generated_package(env)  # normal non-empty candidate
    body = env.client.get(f"/api/handoff/{pid}").json()
    assert body["claims"] and body["evidence"]
    assert body.get("generation") is None


@requires_db
def test_recipient_package_never_exposes_generation_diagnostic(env):
    pid = _generated_package(env)
    code = _publish(env, pid).json()["capability_code"]
    token = _exchange(env, code).json()["session_token"]
    rv = env.client.get("/api/handoff/recipient/package",
                        headers={"Authorization": f"Bearer {token}"}).json()
    assert "generation" not in rv  # creator-only field, never on the recipient view


# ── S17.14 — handoff demo-seed fixture (coherence + end-to-end) ──────────────

def test_handoff_demo_seed_data_is_coherent():
    """Guards scripts/seed_handoff_demo.py so a drifting header/event can't ship a
    demo mailbox that generates an empty package. DB-free: only inspects the
    module's data tables (importing it constructs no DB engine)."""
    from scripts.seed_handoff_demo import DEMO_EVENTS, DEMO_THREADS

    headers = {m["header"] for _s, msgs in DEMO_THREADS for m in msgs}
    sensitive = {m["header"] for _s, msgs in DEMO_THREADS for m in msgs if m.get("sensitivity")}
    noise = {m["header"] for _s, msgs in DEMO_THREADS for m in msgs if m.get("noise")}

    for etype, _summary, hs in DEMO_EVENTS:
        assert etype in {"proposed", "did", "outcome"}  # maps to a claim kind
        assert hs and all(h in headers for h in hs)  # every citation is a real seeded message

    # >=1 event cites only safe (non-sensitive, non-noise) messages, so a default-
    # scope generate produces a non-empty package (claims + evidence).
    excluded = sensitive | noise
    safe_events = [e for e in DEMO_EVENTS if all(h not in excluded for h in e[2])]
    assert len(safe_events) >= 1
    # The demo also exercises BOTH exclusion gates.
    assert sensitive and noise


@requires_db
def test_handoff_demo_seed_generates_publishes_and_excludes(env):
    """End-to-end validation of the demo dataset: seed it into a mailbox, then run
    generate -> publish -> recipient -> export, asserting claims/evidence exist and
    that the sensitive + noise content is excluded everywhere the recipient/export
    can see."""
    from scripts.seed_handoff_demo import DEMO_THREADS, seed_into

    seed_into(env.session, env.mid, env.owner_pid)

    pid = env.client.post(f"/api/handoff/{env.mid}", json={"reason": "vacation", "title": "Coverage"}).json()["id"]
    gen = env.client.post(f"/api/handoff/{pid}/generate").json()
    assert gen["status"] == "generated"
    ev_headers = {e["message_id_header"] for e in gen["evidence"]}

    # Non-empty package with both claim kinds represented.
    assert len(gen["claims"]) >= 6 and len(gen["evidence"]) >= 6
    kinds = {c["kind"] for c in gen["claims"]}
    assert "decision" in kinds and "open_loop" in kinds

    # Sensitive + noise messages are excluded from evidence; sensitivity is counted.
    sensitive = {m["header"] for _s, msgs in DEMO_THREADS for m in msgs if m.get("sensitivity")}
    noise = {m["header"] for _s, msgs in DEMO_THREADS for m in msgs if m.get("noise")}
    assert ev_headers.isdisjoint(sensitive) and ev_headers.isdisjoint(noise)
    assert gen["exclusion_counts"].get("sensitivity", 0) >= 1

    # Every excluded identifier + piece of metadata that must NOT surface anywhere
    # the recipient (or an exported package) can see: sensitive & noise headers,
    # the sensitive subject/body, and the noise subject/sender/domain.
    excluded_headers = sensitive | noise
    excluded_markers = [
        "comp-1@acme.dev",              # sensitive header
        "news-1@techcrunch.com",        # noise header
        "Confidential Q3 comp review",  # sensitive subject
        "compensation adjustments",     # sensitive body
        "Your weekly digest",           # noise subject
        "weekly digest",
        "TechCrunch",                   # noise sender display
        "techcrunch.com",               # noise sender domain
    ]

    # Recipient view renders and never leaks the excluded content — check both the
    # evidence headers AND the full serialized payload (metadata, not just body).
    code = _publish(env, pid).json()["capability_code"]
    token = _exchange(env, code).json()["session_token"]
    rv = env.client.get("/api/handoff/recipient/package",
                        headers={"Authorization": f"Bearer {token}"}).json()
    rv_headers = {e["message_id_header"] for e in rv["evidence"]}
    assert rv_headers.isdisjoint(excluded_headers)
    rv_blob = json_dumps(rv)
    for marker in excluded_markers:
        assert marker not in rv_blob, f"recipient payload leaked excluded marker: {marker}"
    assert "Nexus Auth SSO cutover" in rv_blob  # a safe evidence subject is present

    # Static HTML export renders, includes safe content, and leaks none of the same
    # excluded identifiers/metadata.
    html = env.client.get(f"/api/handoff/{pid}/export.html").text
    assert "Nexus Auth SSO cutover" in html
    for marker in excluded_markers:
        assert marker not in html, f"export HTML leaked excluded marker: {marker}"


@requires_db
def test_handoff_demo_verify_reports_ok_without_side_effects(env):
    """The seed script's --verify helper (S17.15) confirms the mailbox would
    generate a non-empty, exclusion-clean package and leaves NO package OR audit
    rows behind (never publishes, never mints a code/session)."""
    from sqlalchemy import func, select

    from services.db import models as orm
    from scripts.seed_handoff_demo import seed_into, verify_seed

    seed_into(env.session, env.mid, env.owner_pid)

    def _audit_total() -> int:
        s = _fresh()
        try:
            return s.execute(select(func.count()).select_from(orm.HandoffAuditEvent)).scalar_one()
        finally:
            s.close()

    audit_before = _audit_total()
    result = verify_seed(env.session, env.mid)
    assert result["ok"] is True
    assert result["claims"] >= 6 and result["evidence"] >= 6 and result["excluded_ok"] is True

    # No lasting side effects: verify left zero handoff packages for the mailbox,
    # and the candidate_generated audit row it wrote was cleaned up too (total
    # audit-event count is unchanged).
    s = _fresh()
    try:
        n_pkg = s.execute(
            select(func.count()).select_from(orm.HandoffPackage)
            .where(orm.HandoffPackage.mailbox_id == env.mid)
        ).scalar_one()
        assert n_pkg == 0
    finally:
        s.close()
    assert _audit_total() == audit_before
