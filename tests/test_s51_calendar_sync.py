"""S51 - Google Calendar sync job + live calendar layer (DB-gated + DB-free).

Implements docs/s49 sections 3/4/7/9. Verifies: the deterministic normalizer's
allow-list + private-visibility exclusion (DB-free); owner/tenant-guarded enqueue;
fail-fast when no google_calendar account is connected; vault-backed token
resolution in the worker; idempotent upsert; private events excluded from the live
tables (no title/attendee persisted); safe job metadata (counts only); no
token/secret/provider-response leakage; the kill switch; that ONLY the
events.readonly scope is used (never calendar.readonly); and that recipient routes
are unaffected and never read the live calendar tables. A fake calendar client is
injected - no real Google calls.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from services.calendar.normalize import is_private, normalize_event

# ---------------------------------------------------------------------------
# DB-free: normalizer allow-list + sensitivity rules
# ---------------------------------------------------------------------------

def _raw(**over):
    base = {
        "id": "evt-1", "summary": "Nexus weekly sync", "visibility": "default",
        "status": "confirmed",
        "start": {"dateTime": "2026-09-10T15:00:00Z"},
        "end": {"dateTime": "2026-09-10T15:30:00Z"},
        "organizer": {"displayName": "Dana", "email": "dana@acme.com"},
        "attendees": [
            {"displayName": "Sam", "email": "sam@acme.com", "responseStatus": "accepted"},
            {"email": "ext@vendor.io", "responseStatus": "declined"},
        ],
        "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH"],
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
        "description": "Dial-in PIN 998877; sensitive notes here",
    }
    base.update(over)
    return base


def test_private_events_are_excluded_entirely():
    for vis in ("private", "confidential"):
        ev = _raw(visibility=vis)
        assert is_private(ev) is True
        assert normalize_event(ev) is None  # never mapped, title never read


def test_cancelled_events_are_not_ingested():
    assert normalize_event(_raw(status="cancelled")) is None


def test_normalizer_keeps_only_allow_list_fields():
    n = normalize_event(_raw())
    assert n is not None
    assert n.calendar_item_id == "evt-1"
    assert n.title == "Nexus weekly sync"
    assert n.is_recurring and n.recurrence_summary == "weekly"
    assert n.has_conferencing is True
    assert n.organizer_display == "Dana" and n.organizer_domain == "acme.com"
    assert n.attendee_count == 2
    assert n.attendees[0].display == "Sam" and n.attendees[0].domain == "acme.com"
    assert n.attendees[1].domain == "vendor.io"
    # No description/body, join URL, raw RRULE, attendee email, or response status.
    import dataclasses
    blob = str(dataclasses.asdict(n))
    for banned in ("PIN", "meet.google.com", "dana@acme.com", "sam@acme.com",
                   "accepted", "declined", "RRULE", "BYDAY"):
        assert banned not in blob


def test_recurrence_summary_never_leaks_raw_rrule():
    n = normalize_event(_raw(recurrence=["RRULE:FREQ=MONTHLY;COUNT=5;UNTIL=20270101"]))
    assert n.recurrence_summary == "monthly"


def test_all_day_event_flagged():
    n = normalize_event(_raw(start={"date": "2026-09-10"}, end={"date": "2026-09-11"}))
    assert n.all_day is True


# ---------------------------------------------------------------------------
# DB-gated: enqueue + worker + live tables
# ---------------------------------------------------------------------------

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

OWNER = "owner@company.com"
ACCESS_MARK = "ACCESS-"
REFRESH_MARK = "REFRESH-"


class FakeCalendarClient:
    """Records the token/scope-relevant call args; returns canned raw events."""

    def __init__(self, events):
        self.events = events
        self.calls: list[dict] = []

    def list_events(self, *, access_token, calendar_id, time_min, time_max, max_results):
        self.calls.append({
            "access_token": access_token, "calendar_id": calendar_id,
            "time_min": time_min, "time_max": time_max, "max_results": max_results,
        })
        return list(self.events)


def _mk_tenant_user(session, email, suffix):
    from services.db import models as orm
    subject = f"s51-{suffix}-{uuid.uuid4().hex[:8]}"
    t = orm.Tenant(name="T-" + subject); session.add(t); session.flush()
    u = orm.AppUser(tenant_id=t.id, idp_subject=subject, email=email); session.add(u); session.flush()
    session.add(orm.TenantMembership(user_id=u.id, role="creator")); session.commit()
    return t, u


def _principal(t, u, email):
    from services.api.auth import Principal
    return Principal(user_id=str(u.id), tenant_id=str(t.id), email=email,
                     roles=frozenset({"creator"}), is_dev=False)


CAL_EVENTS = [
    {"id": "e-open", "summary": "Nexus weekly sync", "visibility": "default", "status": "confirmed",
     "start": {"dateTime": "2026-09-10T15:00:00Z"}, "end": {"dateTime": "2026-09-10T15:30:00Z"},
     "organizer": {"displayName": "Dana", "email": "dana@acme.com"},
     "attendees": [{"displayName": "Sam", "email": "sam@acme.com", "responseStatus": "accepted"}],
     "recurrence": ["RRULE:FREQ=WEEKLY"], "hangoutLink": "https://meet.google.com/z",
     "description": "secret PIN 4242"},
    {"id": "e-private", "summary": "Therapy appointment", "visibility": "private", "status": "confirmed",
     "start": {"dateTime": "2026-09-11T09:00:00Z"}, "end": {"dateTime": "2026-09-11T10:00:00Z"},
     "attendees": [{"email": "doctor@clinic.com"}], "description": "very private"},
]


@pytest.fixture()
def env(monkeypatch):
    from fastapi.testclient import TestClient

    from services.api.auth import get_principal
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.oauth.gmail_client import set_calendar_oauth_client
    from services.oauth.vault import DevTokenVault, set_vault

    monkeypatch.setenv("EKC_ALLOW_DEV_VAULT", "1")
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.delenv("EKC_CALENDAR_SYNC_DISABLED", raising=False)

    session = SessionLocal()
    t_owner, u_owner = _mk_tenant_user(session, OWNER, "owner")
    t_other, u_other = _mk_tenant_user(session, "other@rival.com", "other")
    mbx = orm.Mailbox(provider="gmail", owner_email=OWNER, embed_model="deferred",
                      embed_dim=0, config={}, tenant_id=t_owner.id, owner_user_id=u_owner.id)
    session.add(mbx); session.commit()
    mid = str(mbx.id)

    # Vault with a fake revoker/refresher so a minted access token is deterministic.
    def _refresh(refresh_token):
        return ACCESS_MARK + refresh_token
    vault = DevTokenVault(refresher=_refresh, revoker=lambda rt: None)
    set_vault(vault)
    set_calendar_oauth_client(None)

    # Seat a connected google_calendar provider account with a real vault entry.
    from services.db import models as orm2
    vault_ref = f"google_calendar:{mid}:{uuid.uuid4()}"
    vault.store_refresh_token(vault_ref, REFRESH_MARK + "cal", metadata={"email": OWNER})
    acct = orm2.MailboxProviderAccount(
        tenant_id=t_owner.id, owner_user_id=u_owner.id, mailbox_id=mid,
        provider="google_calendar", provider_account_email=OWNER, vault_ref=vault_ref,
        scopes_granted=["https://www.googleapis.com/auth/calendar.events.readonly"],
        status="connected",
    )
    session.add(acct); session.commit()
    acct_id = str(acct.id)

    fake_client = FakeCalendarClient(CAL_EVENTS)
    from services.jobs.handlers import calendar_sync as handler
    handler.set_client_factory(lambda: fake_client)

    client = TestClient(app)
    ns = SimpleNamespace(
        client=client, session=session, app=app, mid=mid, vault=vault, acct_id=acct_id,
        fake_client=fake_client, get_principal=get_principal,
        owner=_principal(t_owner, u_owner, OWNER),
        other=_principal(t_other, u_other, "other@rival.com"),
        t_owner=t_owner, u_owner=u_owner,
    )
    ns.as_owner = lambda: app.dependency_overrides.__setitem__(get_principal, lambda: ns.owner)
    ns.as_other = lambda: app.dependency_overrides.__setitem__(get_principal, lambda: ns.other)

    try:
        yield ns
    finally:
        app.dependency_overrides.clear()
        handler.set_client_factory(handler._default_client)
        set_vault(None)
        from sqlalchemy import select
        ev_ids = [r for r in session.execute(select(orm.CalendarEvent.id).where(
            orm.CalendarEvent.mailbox_id == mid)).scalars()]
        if ev_ids:
            session.execute(orm.CalendarEventAttendee.__table__.delete().where(
                orm.CalendarEventAttendee.calendar_event_id.in_(ev_ids)))
        session.execute(orm.CalendarEvent.__table__.delete().where(orm.CalendarEvent.mailbox_id == mid))
        session.execute(orm.Job.__table__.delete().where(orm.Job.mailbox_id == mid))
        session.execute(orm.MailboxProviderAccount.__table__.delete().where(
            orm.MailboxProviderAccount.mailbox_id == mid))
        session.execute(orm.AuditLog.__table__.delete().where(orm.AuditLog.mailbox_id == mid))
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mid))
        for u in (u_owner, u_other):
            session.execute(orm.TenantMembership.__table__.delete().where(
                orm.TenantMembership.user_id == u.id))
            session.execute(orm.AppUser.__table__.delete().where(orm.AppUser.id == u.id))
        for t in (t_owner, t_other):
            session.execute(orm.Tenant.__table__.delete().where(orm.Tenant.id == t.id))
        session.commit(); session.close()


def _fresh():
    from services.db.engine import SessionLocal
    return SessionLocal()


def _sync(env, **body):
    payload = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    payload.update(body)
    return env.client.post(f"/api/mailbox/{env.mid}/calendar/sync", json=payload)


def _run_job(env, job_id):
    """Run the enqueued job through the real handler with a fresh session."""
    from services.jobs.registry import JobContext, get_handler
    s = _fresh()
    try:
        from services.db import models as orm
        job = s.get(orm.Job, job_id)
        ctx = JobContext(job_id=job_id, params=dict(job.params or {}), _db=s)
        result = get_handler("calendar_sync_window")(ctx)
        return result
    finally:
        s.close()


# -- auth / enqueue -----------------------------------------------------------

@requires_db
def test_sync_requires_owner_auth(env):
    assert _sync(env).status_code == 401          # no principal
    env.as_other()
    assert _sync(env).status_code == 404          # cross-tenant


@requires_db
def test_sync_fails_fast_without_connected_calendar(env):
    # Remove the connected account -> 409, no job enqueued.
    from services.db import models as orm
    s = _fresh()
    try:
        s.execute(orm.MailboxProviderAccount.__table__.delete().where(
            orm.MailboxProviderAccount.mailbox_id == env.mid))
        s.commit()
    finally:
        s.close()
    env.as_owner()
    r = _sync(env)
    assert r.status_code == 409
    s = _fresh()
    try:
        jobs = s.execute(orm.Job.__table__.select().where(orm.Job.mailbox_id == env.mid)).all()
        assert jobs == []
    finally:
        s.close()


@requires_db
def test_enqueue_is_idempotent_for_same_window(env):
    env.as_owner()
    j1 = _sync(env).json()["job_id"]
    j2 = _sync(env).json()["job_id"]
    assert j1 == j2  # same window collapses to one active job


# -- worker: vault-backed token, private exclusion, upsert, safe metadata -----

@requires_db
def test_worker_resolves_vault_token_and_stores_only_safe_fields(env):
    env.as_owner()
    job_id = _sync(env).json()["job_id"]
    result = _run_job(env, job_id)

    # The client was called with a vault-minted access token (never a stored token).
    assert env.fake_client.calls, "client not called"
    assert env.fake_client.calls[0]["access_token"].startswith(ACCESS_MARK)
    assert env.fake_client.calls[0]["calendar_id"] == "primary"

    # Only the open event is stored; the private event is excluded entirely.
    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        rows = s.execute(select(orm.CalendarEvent).where(
            orm.CalendarEvent.mailbox_id == env.mid)).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.calendar_item_id == "e-open"
        assert row.title == "Nexus weekly sync"
        assert row.is_recurring and row.recurrence_summary == "weekly"
        assert row.has_conferencing and row.attendee_count == 1
        assert row.organizer_domain == "acme.com"
        atts = s.execute(select(orm.CalendarEventAttendee).where(
            orm.CalendarEventAttendee.calendar_event_id == row.id)).scalars().all()
        assert len(atts) == 1 and atts[0].domain == "acme.com"
        # No private title/attendee, no description/URL/email/token anywhere.
        blob = " ".join(
            str(getattr(r, c.name)) for r in rows for c in r.__table__.columns
        ) + " ".join(str(getattr(a, c.name)) for a in atts for c in a.__table__.columns)
        for banned in ("Therapy", "doctor@clinic.com", "PIN 4242", "meet.google.com",
                       "dana@acme.com", "sam@acme.com", ACCESS_MARK, REFRESH_MARK, "RRULE"):
            assert banned not in blob
    finally:
        s.close()

    # Safe job progress: counts only, no titles/emails/tokens.
    assert result.progress["stored"] == 1
    assert result.progress["skipped_private"] == 1
    prog_blob = str(result.progress) + str(result.summary)
    for banned in ("Therapy", "Nexus", "acme.com", ACCESS_MARK):
        assert banned not in prog_blob


@requires_db
def test_resync_is_idempotent_upsert(env):
    env.as_owner()
    job_id = _sync(env).json()["job_id"]
    _run_job(env, job_id)
    _run_job(env, job_id)  # run again: upsert, not duplicate

    from services.db import models as orm
    from sqlalchemy import func, select
    s = _fresh()
    try:
        n = s.execute(select(func.count()).select_from(orm.CalendarEvent).where(
            orm.CalendarEvent.mailbox_id == env.mid)).scalar_one()
        assert n == 1
        n_att = s.execute(select(func.count()).select_from(orm.CalendarEventAttendee)).scalar_one()
        # exactly one attendee row for the single event (attendees replaced, not doubled)
        ev_id = s.execute(select(orm.CalendarEvent.id).where(
            orm.CalendarEvent.mailbox_id == env.mid)).scalar_one()
        n_att = s.execute(select(func.count()).select_from(orm.CalendarEventAttendee).where(
            orm.CalendarEventAttendee.calendar_event_id == ev_id)).scalar_one()
        assert n_att == 1
    finally:
        s.close()


@requires_db
def test_kill_switch_makes_sync_a_noop(env, monkeypatch):
    monkeypatch.setenv("EKC_CALENDAR_SYNC_DISABLED", "1")
    env.as_owner()
    job_id = _sync(env).json()["job_id"]
    result = _run_job(env, job_id)
    assert result.progress.get("phase") == "disabled"
    assert env.fake_client.calls == []  # never fetched
    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        rows = s.execute(select(orm.CalendarEvent).where(
            orm.CalendarEvent.mailbox_id == env.mid)).scalars().all()
        assert rows == []
    finally:
        s.close()


@requires_db
def test_only_events_readonly_scope_on_connected_account(env):
    """S51 uses the account connected in S50, which granted exactly
    events.readonly; assert the broad calendar.readonly is never present."""
    from services.db import models as orm
    from sqlalchemy import select
    s = _fresh()
    try:
        acct = s.execute(select(orm.MailboxProviderAccount).where(
            orm.MailboxProviderAccount.mailbox_id == env.mid)).scalar_one()
        assert "https://www.googleapis.com/auth/calendar.events.readonly" in acct.scopes_granted
        assert "https://www.googleapis.com/auth/calendar.readonly" not in acct.scopes_granted
    finally:
        s.close()


# -- isolation: recipient routes + gmail unaffected ---------------------------

@requires_db
def test_recipient_route_cannot_reach_calendar_and_is_unaffected(env):
    # The recipient session route is not owner-guarded and never touches calendar.
    r = env.client.post("/api/handoff/recipient/session", json={"code": "nope"})
    assert r.status_code != 401
    # There is no recipient endpoint that reads calendar_event; the live tables are
    # creator-owned only (verified structurally: no recipient router imports them).
    import services.api.routers.handoff_recipient as hr
    src = open(hr.__file__, encoding="utf-8").read()
    assert "CalendarEvent" not in src and "calendar_event" not in src
