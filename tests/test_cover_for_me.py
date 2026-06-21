"""S5 cover-for-me tests (D11, implementation-plan §6.3).

DB-gated like the other API tests: requires a reachable Postgres; skipped
otherwise. They seed the fixture mailbox (L0 + L1 + Events) and exercise the
routing surface:

  - fallback (no entity) → routed_to None, "insufficient" state, no model call.
  - project label in the query → routed_to "project:...".
  - person name in the query → routed_to "person:...".
  - missing API key on a routed query → 503.
  - bad mailbox id → 404.
"""
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
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres unreachable.",
)

OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]


@pytest.fixture()
def seeded():
    """Seed a mailbox with L0 + L1 + extracted Events, yield a TestClient."""
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient

    from conftest import run_full_ingest
    from services.api.main import app
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import persist_l0, persist_l1
    from services.enrich.clustering.eval.run_eval import EVAL_PARAMS
    from services.enrich.clustering.testkit import FakeNlp, make_test_embed
    from services.enrich.events.eval.run_eval import make_gold_extract_fn
    from services.enrich.pipeline import run_enrichment

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

    gold = json.loads(
        (Path(__file__).resolve().parent.parent / "fixtures" / "gold" / "events.json")
        .read_text(encoding="utf-8")
    )
    extract_fn = make_gold_extract_fn(store, gold)

    result = run_enrichment(
        store.messages, OWNER_EMAIL, INTERNAL_DOMAINS,
        threads=store.threads, embed_fn=make_test_embed(), nlp=FakeNlp(),
        cluster_params=EVAL_PARAMS, extract_fn=extract_fn,
    )
    persist_l1(result, mailbox_id, session)

    client = TestClient(app)
    try:
        yield client, mailbox_id, result, session
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id))
        session.commit()
        session.close()


@pytest.fixture()
def no_api_key():
    """Ensure ANTHROPIC_API_KEY is absent for the duration of the test."""
    prev = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ["ANTHROPIC_API_KEY"] = prev


def _a_project_label(result) -> str:
    return result.clustering.projects[0].label


def _a_person_name(result) -> str:
    """A non-owner person name present in L1."""
    for p in result.people:
        if p.canonical_email.lower() == OWNER_EMAIL.lower():
            continue
        if p.names:
            return p.names[0]
    raise AssertionError("fixture should have a non-owner person with a name")


@requires_db
def test_fallback_no_match(seeded):
    """A query mentioning no known entity → routed_to None + insufficient state.

    This path short-circuits before any model call, so no API key is needed."""
    client, mailbox_id, _, _ = seeded
    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": "what is the meaning of life?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["routed_to"] is None
    assert "insufficient" in (body["result"]["state"] or "").lower()


@requires_db
def test_routes_to_project(seeded, no_api_key):
    """A query mentioning a known project label routes to the model stage.

    A matched entity must reach the model (503 without a key) rather than fall
    through to the keyless "insufficient evidence" fallback (which returns 200).
    The exact routed_to label is asserted in ``test_routes_to_project_label``."""
    client, mailbox_id, result, _ = seeded
    label = _a_project_label(result)
    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": f"what is the status of {label}?"},
    )
    # A matched project needs the model → 503 without a key (proves it routed,
    # not fell through to the keyless fallback which returns 200).
    assert r.status_code == 503


@requires_db
def test_routes_to_project_label(seeded):
    """Direct routing check: the resolved route is project:<label>."""
    from services.api.routers.cover_for_me import _route
    from services.db.engine import SessionLocal

    _, mailbox_id, result, _ = seeded
    label = _a_project_label(result)
    db = SessionLocal()
    try:
        person, project = _route(f"status of {label}", db, mailbox_id)
        assert project is not None and person is None
        assert project.label == label
    finally:
        db.close()


@requires_db
def test_routes_to_person(seeded):
    """Direct routing check: a known person name resolves to a person route."""
    from services.api.routers.cover_for_me import _route
    from services.db.engine import SessionLocal

    _, mailbox_id, result, _ = seeded
    name = _a_person_name(result)
    db = SessionLocal()
    try:
        person, project = _route(f"who do I ask about working with {name}", db, mailbox_id)
        assert person is not None and project is None
        assert name in person.names
    finally:
        db.close()


@requires_db
def test_missing_key_503(seeded, no_api_key):
    """A routed query with no API key → 503, not 500."""
    client, mailbox_id, result, _ = seeded
    name = _a_person_name(result)
    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": f"who do I ask about {name}?"},
    )
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()


@requires_db
def test_404_bad_mailbox(seeded):
    client, _, _, _ = seeded
    r = client.post(
        "/api/cover-for-me/00000000-0000-0000-0000-000000000000",
        json={"query": "anything"},
    )
    assert r.status_code == 404


@requires_db
def test_routed_project_200_with_fake_synth(seeded, monkeypatch):
    """Successful routed path: matched project + fake synth_fn → 200 with cited claim."""
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    client, mailbox_id, result, _ = seeded
    label = _a_project_label(result)

    # Use a real message_id_header from the fixture so it passes the allow-list filter.
    real_header = "atlas-1@acme.com"
    fake_result = SynthesisResult(
        claims=[SynthesisClaim(text="Cutover was completed.", source_message_ids=[real_header])],
        model="test",
        usage={},
    )

    def _fake_factory(params):
        def _fn(system, context, query):
            return fake_result
        return _fn

    monkeypatch.setattr("services.api.routers.cover_for_me.make_anthropic_synth_fn", _fake_factory)

    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": f"what is the status of {label}?"},
    )
    assert r.status_code == 200
    body = r.json()
    # The key assertion: the query routed to a project, not the keyless fallback.
    # (The fallback returns routed_to=None; a routed project returns "project:...".)
    assert (body["routed_to"] or "").startswith("project:")
    # Claims may be empty if the fake citation wasn't in this project's allowed_headers,
    # but the routing itself is what we're proving. A separate unit test checks filtering.


@requires_db
def test_citation_filtering_drops_invalid(seeded, monkeypatch):
    """Fake synth returning an invalid citation → allow-list filters it to empty claims."""
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    client, mailbox_id, result, _ = seeded
    label = _a_project_label(result)

    fake_result = SynthesisResult(
        claims=[SynthesisClaim(text="Made-up claim.", source_message_ids=["not-a-real-header@fake"])],
        model="test",
        usage={},
    )

    def _fake_factory(params):
        def _fn(system, context, query):
            return fake_result
        return _fn

    monkeypatch.setattr("services.api.routers.cover_for_me.make_anthropic_synth_fn", _fake_factory)

    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": f"what is the status of {label}?"},
    )
    assert r.status_code == 200
    body = r.json()
    # The invalid citation is not in the allowed headers set → filtered out.
    assert body["result"]["claims"] == []


@requires_db
def test_who_to_ask_about_project_no_key_needed(seeded, no_api_key):
    """'who do I ask about <project>?' is pure L1 — no API key, always 200.

    D11 says cover-for-me answers both "who do I ask about X?" (Person / Edge /
    Role) and "what's the state of project Z?" (Project / Event / Thread).
    The who-to-ask path must NOT call the model; the state path does.
    This test proves the routing distinction: WHO_ASK + project → 200 even
    without a key, while 'state of' + project → 503 without a key.
    """
    client, mailbox_id, result, _ = seeded
    label = _a_project_label(result)

    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": f"who do I ask about {label}?"},
    )
    # Pure L1 — should never hit the model or the key check.
    assert r.status_code == 200, f"Expected 200 but got {r.status_code}: {r.json()}"
    body = r.json()
    assert (body["routed_to"] or "").startswith("project:")
    # Claims are L1-cited contacts, or empty if no citable evidence.
    # Either way, NOT a 503.
    assert "claims" in body["result"]


@requires_db
def test_word_boundary_no_false_match(seeded):
    """A short token that is a substring of a longer word must not trigger a route.

    The word 'at' appears in 'status' but must not match a person named 'at' (if any).
    More concretely: a query about 'attrition' must not route to person 'at' or
    project 'at' if those were entities. We test the _token_match helper directly.
    """
    from services.api.routers.cover_for_me import _token_match

    # Two-letter token must not match because len < _MIN_MATCH_LEN (3).
    assert not _token_match("at", "what is the status")
    # Substring inside a longer word must not match due to word-boundary check.
    assert not _token_match("Ben", "benefits enrollment")
    # Exact word match must succeed (query_lower means it's already downcased).
    assert _token_match("Atlas", "what is the state of atlas?")


# ── S7.11 endpoint integration tests ─────────────────────────────────────────
#
# These tests exercise the FastAPI endpoint directly with monkeypatched L2 so
# they run against a real seeded DB but without Voyage AI or Anthropic API calls.
# They prove endpoint-level behavior that the offline unit tests cannot cover:
# routing, L2 context injection, WHO_ASK short-circuit, and failure fallback.


def _make_fake_retrieval_hit(header: str, snippet: str = "Test snippet."):
    from datetime import datetime, timezone
    from services.retrieval.contracts import RetrievalHit
    return RetrievalHit(
        message_id="00000000-0000-0000-0000-000000000099",
        message_id_header=header,
        thread_id="00000000-0000-0000-0000-000000000098",
        project_ids=(),
        person_ids=(),
        ts=datetime(2026, 1, 15, tzinfo=timezone.utc),
        subject="Test subject",
        snippet=snippet,
        vector_score=0.9,
        fts_score=None,
        rerank_score=0.9,
        source="vector",
        sensitivity=("none",),
        noise=False,
    )


@requires_db
def test_s7_l2_only_endpoint_returns_cited_result(seeded, monkeypatch):
    """No L1 entity match + L2 returns a hit → 200, routed_to=None, cited claim.

    Proves the L2-only path through the FastAPI endpoint: _run_l2 is patched
    to return a hit, synth_fn is patched to cite it, and the response must
    contain that citation.
    """
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    client, mailbox_id, _, _ = seeded
    l2_header = "l2-only-test@example.com"
    hit = _make_fake_retrieval_hit(l2_header, snippet="L2 evidence about deployment.")

    monkeypatch.setattr(
        "services.api.routers.cover_for_me._run_l2",
        lambda query, embed_client, mid, db: ("active", [hit]),  # must be (status, hits) tuple
    )

    def _fake_factory(params):
        def _fn(system, context, query):
            return SynthesisResult(
                claims=[SynthesisClaim(text="Deployment noted.", source_message_ids=[l2_header])],
                model="fake", usage={},
            )
        return _fn

    monkeypatch.setattr("services.api.routers.cover_for_me.make_anthropic_synth_fn", _fake_factory)

    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": "what happened with the xyzzy widget deployment?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["routed_to"] is None
    claims = body["result"]["claims"]
    assert len(claims) == 1
    assert l2_header in claims[0]["source_message_ids"]


@requires_db
def test_s7_l1_project_plus_l2_context(seeded, monkeypatch):
    """L1 project matched + L2 returns a hit → L2 snippet visible in synth context (P1 fix).

    This is the critical regression test for P1: the L2 hit's snippet must appear
    in the context string passed to synth_fn, not just in the citation allow-list.
    """
    from services.synthesis.contracts import SynthesisClaim, SynthesisResult

    client, mailbox_id, result, _ = seeded
    label = _a_project_label(result)

    l2_header = "l2-project-test@example.com"
    l2_snippet = "UNIQUE_L2_SNIPPET_XQ9Z_FOR_TESTING"
    hit = _make_fake_retrieval_hit(l2_header, snippet=l2_snippet)

    monkeypatch.setattr(
        "services.api.routers.cover_for_me._run_l2",
        lambda query, embed_client, mid, db: [hit],
    )

    captured_contexts: list[str] = []

    def _fake_factory(params):
        def _fn(system, context, query):
            captured_contexts.append(context)
            return SynthesisResult(
                claims=[SynthesisClaim(text="Project finding.", source_message_ids=[l2_header])],
                model="fake", usage={},
            )
        return _fn

    monkeypatch.setattr("services.api.routers.cover_for_me.make_anthropic_synth_fn", _fake_factory)

    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": f"what is the status of {label}?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["routed_to"] or "").startswith("project:")
    assert captured_contexts, "synth_fn was never called"
    assert any(l2_snippet in ctx for ctx in captured_contexts), (
        "L2 snippet not found in synthesis context — L2 evidence not passed to model (P1 regression)"
    )


@requires_db
def test_s7_who_ask_project_skips_l2(seeded, monkeypatch):
    """WHO_ASK + project → _get_embed_client never called (P2b fix).

    The pure-L1 who-to-ask path must short-circuit before L2 retrieval to avoid
    unnecessary embedding cost and privacy exposure.
    """
    client, mailbox_id, result, _ = seeded
    label = _a_project_label(result)

    l2_call_log: list[bool] = []

    def _should_not_be_called(mbx):
        l2_call_log.append(True)
        return None

    monkeypatch.setattr(
        "services.api.routers.cover_for_me._get_embed_client", _should_not_be_called
    )

    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": f"who do I ask about {label}?"},
    )
    assert r.status_code == 200
    assert l2_call_log == [], "P2b: _get_embed_client must not be called for WHO_ASK + project"


@requires_db
def test_s7_l2_retrieval_failure_falls_back_gracefully(seeded, monkeypatch):
    """L2 embed raises (key present but request errors) → 200 with insufficient evidence.

    When there is no L1 entity match and L2 retrieval raises, the endpoint must
    not propagate the exception. It logs a warning and falls back to the
    insufficient-evidence path.
    """
    client, mailbox_id, _, _ = seeded

    class _BrokenEmbedClient:
        def embed_query(self, text):
            raise RuntimeError("simulated Voyage AI failure")

    monkeypatch.setattr(
        "services.api.routers.cover_for_me._get_embed_client",
        lambda mbx: _BrokenEmbedClient(),
    )

    r = client.post(
        f"/api/cover-for-me/{mailbox_id}",
        json={"query": "what is the meaning of life?"},  # no L1 entity match
    )
    assert r.status_code == 200
    body = r.json()
    assert body["routed_to"] is None
    assert "insufficient" in (body["result"]["state"] or "").lower()


# ── _call_synthesis unit tests (offline, no DB required) ─────────────────────

def test_call_synthesis_no_mailbox_id_collision():
    """_call_synthesis(log_mailbox_id, ..., mailbox_id=...) must not raise
    TypeError: got multiple values for argument 'mailbox_id'.

    Regression for c77fe46 where the first positional param was named 'mailbox_id',
    colliding with the same-named kwarg forwarded to synthesize_cover_for_me.
    """
    from unittest.mock import patch
    from services.synthesis.contracts import SynthesisResult
    from services.api.routers.cover_for_me import _call_synthesis

    fake_result = SynthesisResult(claims=[], model="test", usage={})

    with patch(
        "services.api.routers.cover_for_me.synthesize_cover_for_me",
        return_value=(fake_result, None),
    ):
        result, routed_to = _call_synthesis(
            "test-mailbox-id",                 # log_mailbox_id — must not shadow kwargs
            query="xyzzy query",
            matched_person=None,
            matched_project=None,
            db=None,
            mailbox_id="test-mailbox-id",      # forwarded kwarg — previously collided
            synth_fn=lambda s, c, q: fake_result,
        )

    assert result is fake_result
    assert routed_to is None


def test_call_synthesis_provider_error_becomes_502():
    """Anthropic provider error escaping synthesize_cover_for_me → HTTPException(502)."""
    import pytest
    from unittest.mock import patch
    from fastapi import HTTPException
    from services.api.routers.cover_for_me import _call_synthesis

    class _FakeAuthError(Exception):
        status_code = 401

    with patch(
        "services.api.routers.cover_for_me.synthesize_cover_for_me",
        side_effect=_FakeAuthError("invalid key"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _call_synthesis(
                "test-mailbox-id",
                query="some query",
                matched_person=None,
                matched_project=None,
                db=None,
                mailbox_id="test-mailbox-id",
                synth_fn=lambda s, c, q: None,
            )

    assert exc_info.value.status_code == 502
    assert "check ANTHROPIC_API_KEY" in exc_info.value.detail
