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
