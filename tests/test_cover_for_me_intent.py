"""Cover-for-me answer-quality intent shaping (S35 follow-up C).

DB-free tests: the intent classifier/shaper is pure, and synthesize_project /
synthesize_contact must forward a caller-supplied ``query`` to the model while
leaving the default (router) behavior unchanged and the citation allow-list
intact. End-to-end wiring through the router lives in test_cover_for_me.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ekc_schemas import (
    Edge,
    Event,
    EventType,
    LabelSource,
    Person,
    Project,
    Role,
    Thread,
)

from services.synthesis.contact_summary import QUERY as CONTACT_QUERY, synthesize_contact
from services.synthesis.contracts import SynthesisClaim, SynthesisResult
from services.synthesis.intent import detect_intent, shape_query
from services.synthesis.project_summary import QUERY as PROJECT_QUERY, synthesize_project

TS = datetime(2026, 4, 1, tzinfo=timezone.utc)


# ── intent classifier (pure) ────────────────────────────────────────────────

def test_detect_intent_classifies_each_shape():
    assert detect_intent("What is blocked for Harbor Billing Migration?") == "blocked"
    assert detect_intent("Anything stuck or waiting on the vendor?") == "blocked"
    assert detect_intent("What are the next steps for Nexus Auth Platform?") == "next_steps"
    assert detect_intent("What action items remain?") == "next_steps"
    assert detect_intent("What changed with Security Audit Remediation?") == "changed"
    assert detect_intent("What's the status of Atlas Data Pipeline?") == "status"
    assert detect_intent("Tell me about the Partner API Launch.") == "summary"


def test_detect_intent_specific_wins_over_status():
    # "blocked" must not be swallowed by a stray "status" token.
    assert detect_intent("What is the status of blockers on Atlas?") == "blocked"


def test_shape_query_includes_question_and_intent_and_citation():
    shaped = shape_query("What is blocked for Harbor Billing Migration?", PROJECT_QUERY)
    assert "Harbor Billing Migration" in shaped          # user's real question preserved
    assert "blocker" in shaped.lower()                    # blocker-shaped instruction
    assert "message_id_header" in shaped                  # citation discipline retained
    assert shaped != PROJECT_QUERY

    todo = shape_query("What are the next steps for Nexus Auth Platform?", PROJECT_QUERY)
    assert "to-do" in todo.lower() or "action" in todo.lower()


def test_shape_query_blank_falls_back_to_default():
    assert shape_query("", PROJECT_QUERY) == PROJECT_QUERY
    assert shape_query("   ", CONTACT_QUERY) == CONTACT_QUERY


# ── synthesize_project / synthesize_contact forward the query ────────────────

def _capturing_synth():
    captured: dict[str, str] = {}

    def _fn(system: str, context: str, query: str) -> SynthesisResult:
        captured["query"] = query
        return SynthesisResult(
            claims=[SynthesisClaim(text="A finding.", source_message_ids=["<a@x>"])],
            model="fake", usage={},
        )

    return _fn, captured


def _project() -> Project:
    return Project(
        id="p1", label="Atlas Migration", label_source=LabelSource.CTFIDF,
        thread_ids=["t1"], start=TS, end=TS, confidence=0.8,
    )


def _thread() -> Thread:
    return Thread(
        id="t1", subject_norm="Atlas cutover",
        participants=["alex@acme.com", "grace@acme.com"], t_start=TS, t_end=TS,
    )


def _event() -> Event:
    return Event(
        id="e1", actor_person_id="person-1", type=EventType.OUTCOME,
        summary="Cutover completed", project_id="p1",
        source_message_ids=["<a@x>"], confidence=0.6,
    )


def test_synthesize_project_forwards_custom_query():
    synth, captured = _capturing_synth()
    shaped = shape_query("What is blocked for Atlas Migration?", PROJECT_QUERY)
    synthesize_project(_project(), [_event()], [_thread()], {"t1": []},
                       synth_fn=synth, query=shaped)
    assert captured["query"] == shaped
    assert "blocker" in captured["query"].lower()


def test_synthesize_project_default_query_unchanged():
    # Router path (no query arg) must still receive the fixed module QUERY.
    synth, captured = _capturing_synth()
    synthesize_project(_project(), [_event()], [_thread()], {"t1": []}, synth_fn=synth)
    assert captured["query"] == PROJECT_QUERY


def test_synthesize_contact_forwards_custom_query():
    synth, captured = _capturing_synth()
    person = Person(id="person-1", canonical_email="grace@acme.com",
                    names=["Grace Mueller"], role=Role.INTERNAL, role_confidence=0.9)
    edge = Edge(person_id="person-1", message_count=5, sent_to_count=2,
                received_count=3, first_contact=TS, last_contact=TS, weight=0.7)
    shaped = shape_query("What are next steps with Grace Mueller?", CONTACT_QUERY)
    synthesize_contact(person, edge, [_thread()], [_event()], synth_fn=synth, query=shaped)
    assert captured["query"] == shaped
    assert "action" in captured["query"].lower() or "to-do" in captured["query"].lower()


def test_custom_query_does_not_bypass_citation_allowlist():
    # A shaped query must not weaken "no citation, no claim": a claim citing a
    # header outside the allow-list (e.g. a sensitive/out-of-scope message) is
    # still dropped before the result leaves the synthesis layer.
    def _synth(system, context, query):
        return SynthesisResult(
            claims=[
                SynthesisClaim(text="Allowed.", source_message_ids=["<a@x>"]),
                SynthesisClaim(text="Leaked.", source_message_ids=["<sensitive@x>"]),
            ],
            model="fake", usage={},
        )

    shaped = shape_query("What is blocked?", PROJECT_QUERY)
    result = synthesize_project(
        _project(), [_event()], [_thread()], {"t1": []},
        synth_fn=_synth, allowed_message_id_headers={"<a@x>"}, query=shaped,
    )
    texts = [c.text for c in result.claims]
    assert "Allowed." in texts
    assert "Leaked." not in texts  # out-of-allow-list citation dropped
    assert all(c.source_message_ids for c in result.claims)  # no uncited claim
