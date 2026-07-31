"""S34 — return handoff / coverage delta (DB-gated).

Seeds two mailboxes (Dana = original covered employee; Alex = coverer), publishes
a coverage package from Dana to Alex, then exercises the reciprocal return flow:
Alex creates a return draft from Alex's OWN mailbox, its scope auto-seeds from the
original, generation produces a coverage-delta package (sensitive/noise excluded),
and publishing defaults the recipient back to Dana. Also checks the auth boundary
and that the original package is untouched.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")


def _db() -> bool:
    if not DATABASE_URL:
        return False
    try:
        from services.db.engine import engine
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db(), reason="DATABASE_URL not set or Postgres unreachable.")

NOW = datetime.now(timezone.utc)
PUB = NOW - timedelta(days=10)   # original published 10 days ago
MSG_TS = NOW - timedelta(days=5)  # coverer activity mid-window


def _person(s, mid, email):
    from services.db import models as orm
    p = orm.Person(mailbox_id=mid, canonical_email=email, names=[email.split("@")[0]],
                   role="internal", role_confidence=0.5)
    s.add(p)
    s.flush()
    return p


def _thread(s, mid):
    from services.db import models as orm
    t = orm.Thread(mailbox_id=mid, provider_thread_ids=[], subject_norm="t",
                   participants=[], t_start=PUB, t_end=NOW, lineage_conflict=False)
    s.add(t)
    s.flush()
    return t


def _msg(s, mid, thread_id, header, sender, *, sensitivity=None, noise=False):
    from services.db import models as orm
    m = orm.Message(
        mailbox_id=mid, message_id_header=header, provider_id=header, thread_id=thread_id,
        sender_email=sender, to_emails=[], cc_emails=[], addresses={}, ts=MSG_TS,
        subject="s", clean_text=f"body for {header}", link_domains=[],
        sensitivity=(sensitivity or ["none"]), noise=noise,
    )
    s.add(m)
    return m


def _project(s, mid, label):
    from services.db import models as orm
    p = orm.Project(mailbox_id=mid, label=label, label_source="ctfidf", start=PUB, end=NOW, confidence=0.9)
    s.add(p)
    s.flush()
    return p


def _event(s, mid, actor_pid, etype, summary, headers, project_id):
    from services.db import models as orm
    s.add(orm.Event(mailbox_id=mid, actor_person_id=actor_pid, type=etype, summary=summary,
                    source_message_ids=headers, project_id=project_id, confidence=0.9))


@pytest.fixture()
def world():
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from services.api.auth import get_principal
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.handoff.tokens import hash_token

    s = SessionLocal()
    t = orm.Tenant(name="S34-" + uuid.uuid4().hex[:8])
    s.add(t)
    s.flush()

    # ── Dana: original covered employee + published coverage package ───────────
    dana = orm.Mailbox(provider="gmail", owner_email="dana@acme.corp", embed_model="deferred",
                       embed_dim=0, config={}, tenant_id=t.id)
    s.add(dana)
    s.flush()
    dana_proj = _project(s, dana.id, "Nexus Auth")
    orig = orm.HandoffPackage(mailbox_id=dana.id, creator_email="dana@acme.corp", status="published",
                              reason="vacation", title="Coverage: Nexus Auth", policy_mode="standard",
                              version=1, lineage_id=str(uuid.uuid4()), published_at=PUB, package_type="coverage")
    s.add(orig)
    s.flush()
    s.add(orm.HandoffScope(package_id=orig.id, date_from=None, date_to=None,
                           included_project_ids=[dana_proj.id], allowed_domains=["partner.example"]))
    s.add(orm.HandoffClaim(package_id=orig.id, kind="decision", text="orig decision",
                           project_id=dana_proj.id, source_message_id_headers=["dana-h1"], confidence=0.9))
    s.add(orm.HandoffEvidence(package_id=orig.id, message_id_header="dana-h1", subject="s",
                              sender_display="Collab", sender_domain="partner.example", body_snapshot="x"))
    s.add(orm.HandoffRecipient(package_id=orig.id, recipient_email="alex@acme.corp",
                               capability_code_hash=hash_token("orig-code-" + uuid.uuid4().hex),
                               expires_at=NOW + timedelta(days=30)))

    # ── Alex: coverer mailbox with matching project + in-window coverage-delta ──
    alex = orm.Mailbox(provider="gmail", owner_email="alex@acme.corp", embed_model="deferred",
                       embed_dim=0, config={}, tenant_id=t.id)
    s.add(alex)
    s.flush()
    alex_actor = _person(s, alex.id, "alex@acme.corp")
    collab = _person(s, alex.id, "collab@partner.example")
    s.add(orm.Identity(mailbox_id=alex.id, email="collab@partner.example", person_id=collab.id, display_names=["Collab"]))
    alex_proj = _project(s, alex.id, "Nexus Auth Rotation")   # label token-overlaps "Nexus Auth"

    th = _thread(s, alex.id)
    _msg(s, alex.id, th.id, "alex-msg-1", "collab@partner.example")           # safe, in scope
    _msg(s, alex.id, th.id, "alex-noise", "collab@partner.example", noise=True)  # noise → excluded
    s.add(orm.ThreadProjectAssignment(thread_id=th.id, project_id=alex_proj.id, weight=1.0, is_primary=True))

    th_sens = _thread(s, alex.id)
    _msg(s, alex.id, th_sens.id, "alex-msg-sens", "collab@partner.example", sensitivity=["legal"])  # sensitive
    s.add(orm.ThreadProjectAssignment(thread_id=th_sens.id, project_id=alex_proj.id, weight=1.0, is_primary=True))

    _event(s, alex.id, alex_actor.id, "did", "Rotated Nexus Auth keys", ["alex-msg-1"], alex_proj.id)
    _event(s, alex.id, alex_actor.id, "proposed", "Follow up on token expiry cleanup", ["alex-msg-1"], alex_proj.id)
    _event(s, alex.id, alex_actor.id, "did", "SENSITIVE decision", ["alex-msg-sens"], alex_proj.id)  # excluded
    _event(s, alex.id, alex_actor.id, "did", "noise-derived", ["alex-noise"], alex_proj.id)          # excluded
    s.commit()

    client = TestClient(app)
    ns = SimpleNamespace(client=client, app=app, session=s, get_principal=get_principal,
                         orig_id=str(orig.id), dana=dana, alex=alex, alex_id=str(alex.id), t=t)
    try:
        yield ns
    finally:
        app.dependency_overrides.clear()
        for mid in (str(dana.id), str(alex.id)):
            pkg_ids = select(orm.HandoffPackage.id).where(orm.HandoffPackage.mailbox_id == mid)
            s.execute(orm.HandoffAuditEvent.__table__.delete().where(orm.HandoffAuditEvent.package_id.in_(pkg_ids)))
            s.execute(orm.HandoffRecipientSession.__table__.delete().where(orm.HandoffRecipientSession.package_id.in_(pkg_ids)))
            s.execute(orm.HandoffPackage.__table__.delete().where(orm.HandoffPackage.mailbox_id == mid))  # cascades children + return_context
            s.execute(orm.Event.__table__.delete().where(orm.Event.mailbox_id == mid))
            s.execute(orm.Identity.__table__.delete().where(orm.Identity.mailbox_id == mid))
            s.execute(orm.Message.__table__.delete().where(orm.Message.mailbox_id == mid))
            s.execute(orm.ThreadProjectAssignment.__table__.delete().where(
                orm.ThreadProjectAssignment.project_id.in_(select(orm.Project.id).where(orm.Project.mailbox_id == mid))))
            s.execute(orm.Project.__table__.delete().where(orm.Project.mailbox_id == mid))
            s.execute(orm.Thread.__table__.delete().where(orm.Thread.mailbox_id == mid))
            s.execute(orm.Person.__table__.delete().where(orm.Person.mailbox_id == mid))
            s.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        s.execute(orm.AppUser.__table__.delete().where(orm.AppUser.tenant_id == t.id))
        s.execute(orm.Tenant.__table__.delete().where(orm.Tenant.id == t.id))
        s.commit()
        s.close()


# ── Happy path (dev principal owns all mailboxes; recipient check relaxed) ─────

@requires_db
def test_return_flow_end_to_end(world, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    from services.db import models as orm
    c = world.client

    # 1) create return draft from the original, using Alex's own mailbox
    r = c.post(f"/api/handoff/{world.orig_id}/return-draft", json={"coverer_mailbox_id": world.alex_id})
    assert r.status_code == 200, r.text
    draft = r.json()
    rid = draft["id"]

    world.session.expire_all()
    pkg = world.session.get(orm.HandoffPackage, rid)
    assert pkg.package_type == "return_delta"
    assert str(pkg.mailbox_id) == world.alex_id            # source = coverer's mailbox
    assert pkg.reason == "coverage_return" and pkg.status == "draft"
    assert str(pkg.lineage_id) != str(world.session.get(orm.HandoffPackage, world.orig_id).lineage_id)

    # 2) scope auto-seeded: original coverage area resolved to Alex-side project(s)
    scope = world.session.get(orm.HandoffScope, rid)
    assert scope.included_project_ids, "return scope should auto-preselect a coverer-side project"
    ctx = world.session.get(orm.HandoffReturnContext, rid)
    assert str(ctx.original_package_id) == world.orig_id
    assert any("nexus" in lbl.lower() for lbl in ctx.carried_area_labels)
    assert ctx.seed_method in ("structured", "mixed")

    # return-context endpoint (creator view, metadata only)
    rc = c.get(f"/api/handoff/{rid}/return-context").json()
    assert rc["suggested_recipient_email"] == "dana@acme.corp"
    assert rc["resolved_project_count"] >= 1

    # 3) generate the coverage delta from Alex's mailbox
    g = c.post(f"/api/handoff/{rid}/generate")
    assert g.status_code == 200, g.text
    claims = world.session.execute(
        orm.HandoffClaim.__table__.select().where(orm.HandoffClaim.package_id == rid)
    ).all()
    evidence = world.session.execute(
        orm.HandoffEvidence.__table__.select().where(orm.HandoffEvidence.package_id == rid)
    ).all()
    assert len(claims) == 2                                # decision + open_loop
    kinds = {row.kind for row in claims}
    assert kinds == {"decision", "open_loop"}
    ev_headers = {row.message_id_header for row in evidence}
    assert ev_headers == {"alex-msg-1"}                    # only the safe, in-scope message

    # sensitive + noise never entered evidence
    assert "alex-msg-sens" not in ev_headers and "alex-noise" not in ev_headers
    # every claim cites in-package evidence
    for row in claims:
        assert row.source_message_id_headers and set(row.source_message_id_headers) <= ev_headers

    # 4) publish → recipient defaults to the original creator (Dana)
    p = c.post(f"/api/handoff/{rid}/publish", json={"recipient_email": ""})
    assert p.status_code == 200, p.text
    assert p.json()["recipient_email"] == "dana@acme.corp"

    # 5) the original package is untouched by publishing the return
    world.session.expire_all()
    orig = world.session.get(orm.HandoffPackage, world.orig_id)
    assert orig.status == "published" and orig.revoked_at is None


@requires_db
def test_scope_seed_does_not_copy_original_project_ids(world):
    """The original mailbox's project ids are provenance only — never used as a
    filter against the coverer's mailbox (mailbox-local id safety, §12)."""
    from services.db import models as orm
    from services.handoff.return_scope import seed_return_scope

    orig = world.session.get(orm.HandoffPackage, world.orig_id)
    seed = seed_return_scope(world.session, original_pkg=orig, coverer_mailbox_id=world.alex_id,
                             date_from=None, date_to=None)
    dana_proj_ids = set(world.session.get(orm.HandoffScope, orig.id).included_project_ids)
    # resolved coverer-side projects must be disjoint from the original's ids
    assert set(seed.included_project_ids).isdisjoint(dana_proj_ids)
    assert seed.included_project_ids  # but non-empty (resolved via label match)
    assert dana_proj_ids <= set(seed.carried_project_ids)  # originals kept as provenance


# ── Auth boundary (production) ────────────────────────────────────────────────

@requires_db
def test_non_owner_cannot_create_return(world, monkeypatch):
    from services.api.auth import Principal
    monkeypatch.setenv("AUTH_MODE", "production")
    # A principal in another tenant who does NOT own Alex's mailbox → 404.
    stranger = Principal(user_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()),
                         email="mallory@evil.example", roles=frozenset({"creator"}), is_dev=False)
    world.app.dependency_overrides[world.get_principal] = lambda: stranger
    r = world.client.post(f"/api/handoff/{world.orig_id}/return-draft", json={"coverer_mailbox_id": world.alex_id})
    assert r.status_code == 404


@requires_db
def test_cross_tenant_original_is_404_indistinguishable_from_missing(world, monkeypatch):
    """A different-tenant user with a VALID source mailbox cannot tell an existing
    (cross-tenant) original apart from a missing id — both return an identical 404,
    so there is no cross-tenant existence oracle."""
    from services.api.auth import Principal
    from services.db import models as orm
    monkeypatch.setenv("AUTH_MODE", "production")

    tb = orm.Tenant(name="S34B-" + uuid.uuid4().hex[:6])
    world.session.add(tb)
    world.session.flush()
    ub = orm.AppUser(tenant_id=tb.id, idp_subject="s34b-" + uuid.uuid4().hex[:8], email="bob@other.corp")
    world.session.add(ub)
    world.session.flush()
    mb = orm.Mailbox(provider="gmail", owner_email="bob@other.corp", embed_model="deferred",
                     embed_dim=0, config={}, tenant_id=tb.id, owner_user_id=ub.id)
    world.session.add(mb)
    world.session.commit()
    try:
        pb = Principal(user_id=str(ub.id), tenant_id=str(tb.id), email="bob@other.corp",
                       roles=frozenset({"creator"}), is_dev=False)
        world.app.dependency_overrides[world.get_principal] = lambda: pb
        # existing original (tenant A) is cross-tenant to Bob → 404
        r_exist = world.client.post(f"/api/handoff/{world.orig_id}/return-draft",
                                    json={"coverer_mailbox_id": str(mb.id)})
        # a missing original id → 404
        r_missing = world.client.post(f"/api/handoff/{uuid.uuid4()}/return-draft",
                                      json={"coverer_mailbox_id": str(mb.id)})
        assert r_exist.status_code == 404 and r_missing.status_code == 404
        assert r_exist.json() == r_missing.json()  # indistinguishable
    finally:
        world.app.dependency_overrides.clear()
        world.session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mb.id))
        world.session.execute(orm.AppUser.__table__.delete().where(orm.AppUser.id == ub.id))
        world.session.execute(orm.Tenant.__table__.delete().where(orm.Tenant.id == tb.id))
        world.session.commit()


@requires_db
def test_capability_session_alone_cannot_create_return(world, monkeypatch):
    """No creator/tenant Principal (a recipient capability session is not a Principal)
    → 401 in production. The endpoint requires signed-in creator/source-mailbox auth."""
    monkeypatch.setenv("AUTH_MODE", "production")
    world.app.dependency_overrides.clear()  # no principal source → get_principal 401
    r = world.client.post(f"/api/handoff/{world.orig_id}/return-draft",
                          json={"coverer_mailbox_id": world.alex_id})
    assert r.status_code == 401


@requires_db
def test_non_published_original_rejected(world, monkeypatch):
    """Only a PUBLISHED original can seed a return; a same-tenant non-published
    original is rejected with 409 (tenant-visible, wrong state)."""
    from services.db import models as orm
    monkeypatch.setenv("AUTH_MODE", "dev")
    orig = world.session.get(orm.HandoffPackage, world.orig_id)
    for bad in ("draft", "generated", "revoked", "superseded"):
        orig.status = bad
        world.session.commit()
        r = world.client.post(f"/api/handoff/{world.orig_id}/return-draft",
                              json={"coverer_mailbox_id": world.alex_id})
        assert r.status_code == 409, f"{bad} -> {r.status_code}"
    orig.status = "published"
    world.session.commit()


@requires_db
def test_owner_but_wrong_recipient_email_denied(world, monkeypatch):
    """Owns the coverer mailbox but is not the original recipient → 403."""
    from services.api.auth import Principal
    from services.db import models as orm
    monkeypatch.setenv("AUTH_MODE", "production")

    # Bind Alex's mailbox to a user whose email is NOT the original recipient.
    u = orm.AppUser(tenant_id=world.t.id, idp_subject="s34-" + uuid.uuid4().hex[:8], email="notalex@acme.corp")
    world.session.add(u)
    world.session.flush()
    alex = world.session.get(orm.Mailbox, world.alex_id)
    alex.owner_user_id = u.id
    alex.owner_email = "notalex@acme.corp"
    world.session.commit()

    principal = Principal(user_id=str(u.id), tenant_id=str(world.t.id), email="notalex@acme.corp",
                          roles=frozenset({"creator"}), is_dev=False)
    world.app.dependency_overrides[world.get_principal] = lambda: principal
    r = world.client.post(f"/api/handoff/{world.orig_id}/return-draft", json={"coverer_mailbox_id": world.alex_id})
    assert r.status_code == 403


@requires_db
def test_recipient_snapshot_only_invariant_untouched(world):
    from services.hosted_readiness import check_recipient_snapshot_only
    assert check_recipient_snapshot_only().status == "pass"
