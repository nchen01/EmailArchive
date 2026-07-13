"""S16.0 — date-range ingest: provider-neutral options, validation, Gmail query,
pipeline windowing, and CLI/backend date-window semantics.

All DB-free and Gmail-free (the Gmail service is mocked). Live Gmail validation
is manual/operator only (see docs/s16-date-range-ingest-plan.md Manual Validation).
"""
from __future__ import annotations

from datetime import date

import pytest

from services.ingest.list_options import (
    DateWindowError,
    ListOptions,
    parse_date,
    parse_date_window,
)
from services.ingest.providers.gmail import build_gmail_query


# ── ListOptions + validation (tickets 1, 3-CLI, 7-API share this) ─────────────

def test_parse_date_window_none_is_open_ended():
    opts = parse_date_window(None, None)
    assert opts == ListOptions(date_from=None, date_to=None)
    assert not opts.is_windowed()


def test_parse_date_window_only_from():
    opts = parse_date_window("2026-04-01", None)
    assert opts.date_from == date(2026, 4, 1)
    assert opts.date_to is None
    assert opts.is_windowed()


def test_parse_date_window_only_to():
    opts = parse_date_window(None, "2026-06-30")
    assert opts.date_from is None
    assert opts.date_to == date(2026, 6, 30)
    assert opts.is_windowed()


def test_parse_date_window_both():
    opts = parse_date_window("2026-04-01", "2026-06-30")
    assert opts.date_from == date(2026, 4, 1)
    assert opts.date_to == date(2026, 6, 30)


@pytest.mark.parametrize("bad", ["2026-13-40", "last-week", "4/1/26", "2026-1-1x", "20260401"])
def test_parse_date_invalid_format_raises(bad):
    with pytest.raises(DateWindowError):
        parse_date(bad)


def test_parse_date_window_from_after_to_raises():
    with pytest.raises(DateWindowError) as exc:
        parse_date_window("2026-06-30", "2026-04-01")
    assert "after" in str(exc.value).lower()


def test_parse_date_blank_is_none():
    assert parse_date("") is None
    assert parse_date("   ") is None
    assert parse_date(None) is None


# ── Gmail query construction (ticket 2) ───────────────────────────────────────

def test_gmail_query_none_when_no_window():
    assert build_gmail_query(None) is None
    assert build_gmail_query(ListOptions()) is None


def test_gmail_query_only_from():
    q = build_gmail_query(ListOptions(date_from=date(2026, 4, 1)))
    assert q == "after:2026/04/01"


def test_gmail_query_only_to_shifts_before_one_day():
    # Inclusive date_to=2026-06-30 -> Gmail before:2026/07/01 (exclusive next day).
    q = build_gmail_query(ListOptions(date_to=date(2026, 6, 30)))
    assert q == "before:2026/07/01"


def test_gmail_query_both_bounds():
    q = build_gmail_query(
        ListOptions(date_from=date(2026, 4, 1), date_to=date(2026, 6, 30))
    )
    assert q == "after:2026/04/01 before:2026/07/01"


def test_gmail_query_month_end_year_boundary_shift():
    # Dec 31 inclusive -> before Jan 1 next year (day + month + year rollover).
    q = build_gmail_query(ListOptions(date_to=date(2026, 12, 31)))
    assert q == "before:2027/01/01"


def test_gmail_query_contains_only_formatted_dates():
    # No user text is interpolated: the query is exactly the two date operators.
    q = build_gmail_query(
        ListOptions(date_from=date(2026, 1, 2), date_to=date(2026, 3, 4))
    )
    assert q == "after:2026/01/02 before:2026/03/05"


# ── GmailProvider.list_ids passes q and ignores sync token when windowed ──────

def _mock_gmail_provider():
    from unittest.mock import MagicMock
    from services.ingest.params import IngestParams
    from services.ingest.providers.gmail import GmailProvider

    provider = GmailProvider(params=IngestParams(), mailbox_id="mb")
    svc = MagicMock()
    users = svc.users.return_value
    users.getProfile.return_value.execute.return_value = {"historyId": "111"}
    users.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}],
    }
    provider._service = svc
    return provider, svc, users


def test_gmail_list_ids_windowed_passes_query_and_skips_history():
    provider, svc, users = _mock_gmail_provider()
    opts = ListOptions(date_from=date(2026, 4, 1), date_to=date(2026, 6, 30))

    # A stored token is present but must be IGNORED for a windowed run.
    ids = list(provider.list_ids("STALE_TOKEN", opts))

    assert ids == ["m1", "m2"]
    # history().list must NOT be called (no incremental path for windowed runs).
    users.history.return_value.list.assert_not_called()
    # messages.list was called with the date-window q=.
    _, kwargs = users.messages.return_value.list.call_args
    assert kwargs.get("q") == "after:2026/04/01 before:2026/07/01"


def test_gmail_list_ids_no_window_no_query_kwarg():
    provider, svc, users = _mock_gmail_provider()
    list(provider.list_ids(None, None))
    _, kwargs = users.messages.return_value.list.call_args
    assert "q" not in kwargs  # unchanged behavior: no q when no window


def test_gmail_list_ids_token_only_uses_incremental():
    provider, svc, users = _mock_gmail_provider()
    users.history.return_value.list.return_value.execute.return_value = {
        "history": [{"messagesAdded": [{"message": {"id": "h1"}}]}],
    }
    ids = list(provider.list_ids("TOKEN", None))
    assert ids == ["h1"]  # incremental path preserved when no window
    users.messages.return_value.list.assert_not_called()


# ── Pipeline windowing: options trigger list path + bypass since_token ────────

class _FakeProvider:
    """Records the (since_token, options) list_ids was called with."""
    def __init__(self, ids):
        self._ids = ids
        self.calls: list[tuple] = []

    def authorize(self, grant):
        pass

    def list_ids(self, since_token, options=None):
        self.calls.append((since_token, options))
        yield from self._ids

    def fetch(self, provider_id):
        from services.ingest.providers.base import MimePart, RawMessage
        return RawMessage(
            provider_id=provider_id,
            provider_thread_id=f"t-{provider_id}",
            headers={
                "Message-ID": f"<{provider_id}@acme.corp>",
                "From": "a@acme.corp", "To": "b@acme.corp",
                "Date": "Wed, 01 Apr 2026 10:00:00 +0000", "Subject": "s",
            },
            mime_parts=[MimePart(type="text/plain", bytes=b"hi", charset="utf-8")],
        )

    def fetch_all(self):
        for i in self._ids:
            yield self.fetch(i)

    def sync_token(self):
        return "tok"


def test_pipeline_windowed_ignores_since_token(monkeypatch):
    from services.ingest import pipeline as pl

    fake = _FakeProvider(["a", "b"])
    monkeypatch.setattr(pl, "make_provider", lambda cfg: fake)

    cfg = pl.IngestConfig(
        provider="gmail", owner_email="o@acme.corp",
        since_token="SHOULD_BE_IGNORED",
        list_options=ListOptions(date_from=date(2026, 4, 1)),
    )
    pl.run_ingest(cfg)

    assert len(fake.calls) == 1
    since_token, options = fake.calls[0]
    assert since_token is None, "windowed run must bypass the sync token"
    assert options.date_from == date(2026, 4, 1)


def test_pipeline_no_window_unchanged(monkeypatch):
    from services.ingest import pipeline as pl

    fake = _FakeProvider(["a"])
    monkeypatch.setattr(pl, "make_provider", lambda cfg: fake)

    # No window, no token, no cap -> fetch_all path (list_ids not used).
    cfg = pl.IngestConfig(provider="gmail", owner_email="o@acme.corp")
    pl.run_ingest(cfg)
    assert fake.calls == [], "no-window/no-token/no-cap path must use fetch_all"


# ── CLI: sync-token decision precedence (tickets 5, 6) ────────────────────────

def test_decide_sync_token_precedence():
    from scripts.gmail_smoke_ingest import _decide_sync_token

    # Windowed snapshot: never save, even with a valid token and no cap.
    tok, status = _decide_sync_token(windowed=True, hit_cap=False, new_sync_token="H123456789ABCDEF0")
    assert tok is None and "date-windowed snapshot" in status

    # Capped run: never save.
    tok, status = _decide_sync_token(windowed=False, hit_cap=True, new_sync_token="H123456789ABCDEF0")
    assert tok is None and "capped" in status

    # Empty historyId: never save.
    tok, status = _decide_sync_token(windowed=False, hit_cap=False, new_sync_token="")
    assert tok is None and "historyId" in status

    # Normal complete run: save.
    tok, status = _decide_sync_token(windowed=False, hit_cap=False, new_sync_token="H123456789ABCDEF0")
    assert tok == "H123456789ABCDEF0"

    # Windowed takes precedence over cap.
    tok, _ = _decide_sync_token(windowed=True, hit_cap=True, new_sync_token="H123456789ABCDEF0")
    assert tok is None


# ── CLI: plan-only never fetches bodies and never persists (ticket 4) ─────────

def _cli_args(**kw):
    import argparse
    defaults = dict(
        smoke_check=False, plan_only=False, dry_run=False, show_body=False,
        max_messages=200, owner_email="o@acme.corp", internal_domains=[],
        date_from=None, date_to=None,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_cli_plan_only_no_fetch_no_persist(monkeypatch):
    from datetime import datetime, timezone

    import services.db.store as store
    from scripts import gmail_smoke_ingest as cli

    audits: list[dict] = []
    monkeypatch.setattr(store, "write_audit_event", lambda *a, **kw: audits.append(kw))
    # Any persistence attempt should blow up the test loudly.
    for name in ("persist_l0", "persist_l1", "save_sync_token"):
        monkeypatch.setattr(store, name, lambda *a, **k: (_ for _ in ()).throw(
            AssertionError(f"plan-only must not call {name}")))

    class PlanProvider:
        def list_ids(self, since_token, options=None):
            for i in range(3):
                yield f"id{i}"

        def fetch(self, provider_id):
            raise AssertionError("plan-only must not fetch raw message bodies")

        def sync_token(self):
            return "TOKEN"

    opts = ListOptions(date_from=date(2026, 4, 1), date_to=date(2026, 6, 30))
    args = _cli_args(plan_only=True, date_from="2026-04-01", date_to="2026-06-30")

    cli._run_post_start(
        args, PlanProvider(), object(), "mb-1", "actor",
        datetime.now(timezone.utc), None, None, opts,
    )

    assert len(audits) == 1
    assert audits[0]["action"] == "ingest_finish"
    assert audits[0]["message_count"] == 3  # exact count, under the cap


def test_cli_argparser_accepts_new_flags(monkeypatch):
    import sys
    from scripts import gmail_smoke_ingest as cli

    argv = [
        "prog", "--owner-email", "o@acme.corp", "--confirm",
        "--date-from", "2026-04-01", "--date-to", "2026-06-30", "--plan-only",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    ns = cli.parse_args()
    assert ns.date_from == "2026-04-01"
    assert ns.date_to == "2026-06-30"
    assert ns.plan_only is True


# ── Backend demo endpoint (ticket 7) — DB-gated ───────────────────────────────

import os  # noqa: E402


def _db_reachable() -> bool:
    if not os.environ.get("DATABASE_URL"):
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


class _FakePreviewProvider:
    """Lists IDs; fetch() must never be called during preview."""
    def list_ids(self, since_token, options=None):
        for i in range(4):
            yield f"g{i}"

    def fetch(self, provider_id):
        raise AssertionError("preview must not fetch raw message bodies")

    def sync_token(self):
        return "TOK"


@pytest.fixture()
def gmail_mailbox():
    from fastapi.testclient import TestClient

    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email="demo.handoff@acme.corp",
        embed_model="deferred", embed_dim=0, config={"internal_domains": ["acme.corp"]},
    )
    session.add(mbx)
    session.commit()
    mailbox_id = str(mbx.id)
    client = TestClient(app)
    try:
        yield client, mailbox_id
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id))
        session.commit()
        session.close()


@requires_db
def test_api_preview_no_fetch_no_persist(gmail_mailbox, monkeypatch):
    from services.api.routers import gmail_ingest as router

    monkeypatch.setattr(router, "_provider_for", lambda _id: _FakePreviewProvider())
    client, mailbox_id = gmail_mailbox

    resp = client.post(
        f"/api/gmail-ingest/{mailbox_id}/preview",
        json={"date_from": "2026-04-01", "date_to": "2026-06-30", "max_messages": 500},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 4
    assert body["cap_hit"] is False
    assert body["persisted"] is False
    assert body["provider_filter_applied"] is True
    assert "preview" in body["sync_token_disposition"]


@requires_db
def test_api_preview_invalid_date_422(gmail_mailbox, monkeypatch):
    from services.api.routers import gmail_ingest as router

    # Provider should never be built for an invalid request.
    monkeypatch.setattr(router, "_provider_for", lambda _id: (_ for _ in ()).throw(
        AssertionError("must not build provider on invalid date")))
    client, mailbox_id = gmail_mailbox

    resp = client.post(
        f"/api/gmail-ingest/{mailbox_id}/preview",
        json={"date_from": "2026-13-40"},
    )
    assert resp.status_code == 422

    resp2 = client.post(
        f"/api/gmail-ingest/{mailbox_id}/preview",
        json={"date_from": "2026-06-30", "date_to": "2026-04-01"},
    )
    assert resp2.status_code == 422


@requires_db
def test_api_ingest_requires_confirm(gmail_mailbox, monkeypatch):
    from services.api.routers import gmail_ingest as router

    monkeypatch.setattr(router, "run_windowed_ingest", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not ingest without confirm")))
    client, mailbox_id = gmail_mailbox

    resp = client.post(
        f"/api/gmail-ingest/{mailbox_id}/ingest",
        json={"date_from": "2026-04-01", "date_to": "2026-06-30"},  # no confirm
    )
    assert resp.status_code == 400


@requires_db
def test_api_ingest_confirm_shares_snapshot_semantics(gmail_mailbox, monkeypatch):
    from services.api.routers import gmail_ingest as router

    captured = {}

    def _fake_ingest(session, **kwargs):
        captured.update(kwargs)
        return {
            "messages": 4, "threads": 2, "people": 3, "edges": 1, "hit_cap": False,
            "sync_token_disposition": "not_saved (date-windowed snapshot)", "persisted": True,
        }

    monkeypatch.setattr(router, "run_windowed_ingest", _fake_ingest)
    client, mailbox_id = gmail_mailbox

    resp = client.post(
        f"/api/gmail-ingest/{mailbox_id}/ingest",
        json={"date_from": "2026-04-01", "date_to": "2026-06-30", "max_messages": 100, "confirm": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["persisted"] is True
    assert "date-windowed snapshot" in body["sync_token_disposition"]
    # Confirms the endpoint forwarded the validated window + cap to the shared runner.
    assert captured["options"].date_from == date(2026, 4, 1)
    assert captured["options"].date_to == date(2026, 6, 30)
    assert captured["max_messages"] == 100


@requires_db
def test_api_bad_mailbox_404(gmail_mailbox):
    client, _ = gmail_mailbox
    resp = client.post(
        "/api/gmail-ingest/not-a-uuid/preview",
        json={"date_from": "2026-04-01"},
    )
    assert resp.status_code == 404
