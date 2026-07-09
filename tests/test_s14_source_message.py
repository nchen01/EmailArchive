"""S14 — evidence & source-navigation polish.

Two groups:

1. DB-free unit tests for the pure evidence helpers (``gmail_search_url``,
   ``sender_fields``) — no Postgres, no Voyage.

2. DB-gated tests for ``GET /api/source-message/{mailbox_id}`` and the upgraded
   ``_build_supporting_evidence`` field population. These seed the fixture
   mailbox (L0 only — the endpoint does not need L1) and prove:
     - a clean citation returns the safe DTO and nothing else;
     - a sensitive message returns 404 (no existence oracle);
     - a non-sensitive message in a thread with a sensitive sibling returns 404
       (whole-thread exclusion);
     - missing / wrong-mailbox headers return 404;
     - the snippet is bounded and no body/MIME/token field leaks;
     - the Gmail ``open_url`` is built for the gmail provider.
"""
from __future__ import annotations

import os
from collections import defaultdict

import pytest

from services.api.evidence import gmail_search_url, sender_fields

DATABASE_URL = os.environ.get("DATABASE_URL")

_GMAIL_PREFIX = "https://mail.google.com/mail/#search/rfc822msgid:"

# The exact public fields of SourceMessageDetail — used to prove nothing else
# (body, MIME, sensitivity, provider internals) ever leaks into the response.
_ALLOWED_KEYS = {
    "message_id_header", "subject", "date", "sender_display", "sender_domain",
    "provider_type", "snippet", "source_type", "open_url",
}


# ── DB-free helper unit tests ────────────────────────────────────────────────

def test_gmail_search_url_gmail_only():
    assert gmail_search_url("msgraph", "atlas-1@acme.com") is None
    assert gmail_search_url(None, "atlas-1@acme.com") is None
    assert gmail_search_url("gmail", "") is None


def test_gmail_search_url_encodes_and_strips_brackets():
    url = gmail_search_url("gmail", "<atlas 1@acme.com>")
    assert url is not None
    assert url.startswith(_GMAIL_PREFIX)
    # '@' and space are percent-encoded; angle brackets are stripped first.
    assert "%40" in url
    assert "<" not in url and ">" not in url
    assert " " not in url


def test_sender_fields_prefers_display_name():
    addresses = {"sender": {"email": "dana@acme.com", "display_names": ["Dana Ray"]}}
    display, domain = sender_fields("dana@acme.com", addresses)
    assert display == "Dana Ray"
    assert domain == "acme.com"


def test_sender_fields_falls_back_to_local_part():
    display, domain = sender_fields("dana@acme.com", None)
    assert display == "dana"
    assert domain == "acme.com"


def test_sender_fields_handles_json_string_and_empty():
    display, domain = sender_fields(
        "x@y.com", '{"sender": {"display_names": ["X Y"]}}'
    )
    assert display == "X Y"
    assert domain == "y.com"
    # No email at all → empty domain, empty-ish display.
    d2, dom2 = sender_fields("", None)
    assert dom2 == ""


# ── DB-gated endpoint tests ──────────────────────────────────────────────────

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

OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]


@pytest.fixture()
def seeded_l0():
    """Seed the fixture mailbox (L0 only) and yield (client, mailbox_id, session)."""
    from fastapi.testclient import TestClient

    from conftest import run_full_ingest
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import persist_l0

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail", owner_email=OWNER_EMAIL,
        embed_model="test-none", embed_dim=0,
        config={"internal_domains": INTERNAL_DOMAINS},
    )
    session.add(mbx)
    session.commit()
    mailbox_id = str(mbx.id)

    store = run_full_ingest()
    persist_l0(store, mailbox_id, session)

    client = TestClient(app)
    try:
        yield client, mailbox_id, session
    finally:
        session.execute(
            orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id)
        )
        session.commit()
        session.close()


def _classify_headers(session, mailbox_id):
    """Return (clean, sensitive, tainted) message_id_headers from the seeded mbx.

    clean    = non-sensitive message in a thread with no sensitive sibling.
    sensitive= a message with a non-{none} sensitivity tag.
    tainted  = non-sensitive message in a thread that DOES have a sensitive
               sibling (may be None if the fixture has no such thread).
    """
    from sqlalchemy import select

    from services.db import models as orm

    msgs = session.execute(
        select(orm.Message).where(orm.Message.mailbox_id == mailbox_id)
    ).scalars().all()

    thread_has_sensitive = defaultdict(bool)
    for m in msgs:
        if list(m.sensitivity) != ["none"]:
            thread_has_sensitive[m.thread_id] = True

    clean = sensitive = tainted = None
    for m in msgs:
        is_sensitive = list(m.sensitivity) != ["none"]
        if is_sensitive:
            sensitive = sensitive or m.message_id_header
        elif thread_has_sensitive[m.thread_id]:
            tainted = tainted or m.message_id_header
        else:
            clean = clean or m.message_id_header
    return clean, sensitive, tainted


@requires_db
def test_source_message_clean_returns_safe_dto(seeded_l0):
    client, mailbox_id, session = seeded_l0
    clean, _sensitive, _tainted = _classify_headers(session, mailbox_id)
    assert clean is not None, "fixture should have at least one clean message"

    resp = client.get(
        f"/api/source-message/{mailbox_id}",
        params={"message_id_header": clean},
    )
    assert resp.status_code == 200
    body = resp.json()

    # No unexpected keys → no body/MIME/token/sensitivity leakage.
    assert set(body.keys()) == _ALLOWED_KEYS
    assert body["message_id_header"] == clean
    assert body["provider_type"] == "gmail"
    assert len(body["snippet"]) <= 200
    assert body["sender_domain"]  # non-empty domain
    assert body["open_url"].startswith(_GMAIL_PREFIX)


@requires_db
def test_source_message_sensitive_returns_404(seeded_l0):
    client, mailbox_id, session = seeded_l0
    _clean, sensitive, _tainted = _classify_headers(session, mailbox_id)
    if sensitive is None:
        pytest.skip("fixture has no sensitive message to exercise exclusion")

    resp = client.get(
        f"/api/source-message/{mailbox_id}",
        params={"message_id_header": sensitive},
    )
    assert resp.status_code == 404


@requires_db
def test_source_message_whole_thread_exclusion(seeded_l0):
    client, mailbox_id, session = seeded_l0
    _clean, _sensitive, tainted = _classify_headers(session, mailbox_id)
    if tainted is None:
        pytest.skip("fixture has no non-sensitive sibling in a sensitive thread")

    # The message itself is {none}, but a sibling in its thread is sensitive →
    # whole-thread exclusion must 404 it.
    resp = client.get(
        f"/api/source-message/{mailbox_id}",
        params={"message_id_header": tainted},
    )
    assert resp.status_code == 404


@requires_db
def test_source_message_whole_thread_exclusion_synthetic(seeded_l0):
    """Deterministic whole-thread guard: a non-sensitive message in a thread that
    contains a sensitive sibling must 404, and the sensitive message itself 404s.

    Seeded directly (not relying on the fixture having this shape) so the privacy
    guarantee is actually exercised, not skipped.
    """
    import uuid
    from datetime import datetime, timezone

    from services.db import models as orm

    client, mailbox_id, session = seeded_l0
    tid = str(uuid.uuid4())
    ts = datetime(2026, 2, 1, tzinfo=timezone.utc)
    # Flush the thread before the messages: Message.thread_id is a raw ForeignKey
    # with no ORM relationship() to Thread, so the unit of work does not reliably
    # order the parent insert first.
    session.add(orm.Thread(
        id=tid, mailbox_id=mailbox_id, subject_norm="mixed", t_start=ts, t_end=ts,
    ))
    session.flush()
    session.add_all([
        orm.Message(
            mailbox_id=mailbox_id, message_id_header="wt-clean@acme.com",
            provider_id="wt_clean", thread_id=tid, sender_email="a@acme.com",
            ts=ts, subject="Clean sibling", clean_text="ok", sensitivity=["none"],
        ),
        orm.Message(
            mailbox_id=mailbox_id, message_id_header="wt-sensitive@acme.com",
            provider_id="wt_sens", thread_id=tid, sender_email="hr@acme.com",
            ts=ts, subject="HR matter", clean_text="secret", sensitivity=["hr"],
        ),
    ])
    session.commit()

    # Clean sibling is itself {none}, but its thread has a sensitive message.
    clean_resp = client.get(
        f"/api/source-message/{mailbox_id}",
        params={"message_id_header": "wt-clean@acme.com"},
    )
    assert clean_resp.status_code == 404

    sensitive_resp = client.get(
        f"/api/source-message/{mailbox_id}",
        params={"message_id_header": "wt-sensitive@acme.com"},
    )
    assert sensitive_resp.status_code == 404


@requires_db
def test_source_message_missing_header_404(seeded_l0):
    client, mailbox_id, _session = seeded_l0
    resp = client.get(
        f"/api/source-message/{mailbox_id}",
        params={"message_id_header": "does-not-exist@nowhere.example"},
    )
    assert resp.status_code == 404


@requires_db
def test_source_message_wrong_mailbox_404(seeded_l0):
    client, _mailbox_id, session = seeded_l0
    clean, _s, _t = _classify_headers(session, _mailbox_id)
    other = "00000000-0000-0000-0000-0000000000ff"
    resp = client.get(
        f"/api/source-message/{other}",
        params={"message_id_header": clean or "x@y.z"},
    )
    assert resp.status_code == 404


@requires_db
def test_build_supporting_evidence_populates_s14_fields(seeded_l0):
    """The upgraded builder fills sender/source_type/open_url for a cited header."""
    from services.api.routers.cover_for_me import _build_supporting_evidence
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    _client, mailbox_id, session = seeded_l0
    clean, _s, _t = _classify_headers(session, mailbox_id)
    assert clean is not None

    result = SynthesisResult(
        claims=[SynthesisClaim(text="x", source_message_ids=[clean])],
        model="fake", usage={},
    )
    evidence = _build_supporting_evidence(result, [], session, mailbox_id, "gmail")

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.message_id_header == clean
    assert ev.source_type == "l1_structured"
    assert ev.sender_domain
    assert ev.open_url.startswith(_GMAIL_PREFIX)
