"""Track-A event-extraction tests (S4 ticket 4.5, DoD §10).

All offline — the LLM is injected as a deterministic fake ``extract_fn``. These
exercise the orchestration logic in ``services/enrich/events.py``:
sensitivity exclusion, epistemic typing pass-through, actor skip on unresolvable
mention, and the >=1-citation requirement.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ekc_schemas import Address, Message, Sensitivity, Thread

from services.enrich.events import (
    ExtractedEventRaw,
    ThreadContext,
    extract_events,
)

TS = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)

# Stable person ids for the tests.
PID_RAJ = "11111111-1111-1111-1111-111111111111"
PID_GRACE = "22222222-2222-2222-2222-222222222222"
PID_OWNER = "33333333-3333-3333-3333-333333333333"

E2P = {
    "raj@acme.com": PID_RAJ,
    "grace@acme.com": PID_GRACE,
    "alex@acme.com": PID_OWNER,
}


def _msg(header: str, sender: str, text: str, sensitivity=None) -> Message:
    return Message(
        id=f"id-{header}",
        message_id_header=header,
        provider_id=f"p-{header}",
        thread_id="t1",
        sender=Address(raw=sender, email=sender),
        to=[Address(raw="alex@acme.com", email="alex@acme.com")],
        ts=TS,
        subject="Atlas",
        clean_text=text,
        sensitivity=sensitivity or [Sensitivity.NONE],
    )


def _thread(message_ids: list[str]) -> Thread:
    return Thread(
        id="t1",
        subject_norm="Atlas",
        participants=["alex@acme.com", "raj@acme.com", "grace@acme.com"],
        message_ids=message_ids,
        t_start=TS,
        t_end=TS,
    )


def test_sensitivity_exclusion():
    """A thread with any non-NONE message produces 0 events."""
    msgs = [
        _msg("<a@x>", "raj@acme.com", "I'll re-shard the cluster", [Sensitivity.HR]),
    ]
    thread = _thread(["id-<a@x>"])

    def fake(ctx: ThreadContext):
        return [
            ExtractedEventRaw(
                actor_name_or_email="raj@acme.com",
                type="proposed",
                summary="Will re-shard the index",
                source_message_id_headers=["<a@x>"],
            )
        ]

    events = extract_events(
        [thread], {"t1": msgs}, [], E2P, PID_OWNER, extract_fn=fake
    )
    assert events == []


def test_epistemic_types():
    """'I'll re-shard' → proposed; 'Staging cutover completed' → outcome."""
    msgs = [
        _msg("<a@x>", "raj@acme.com", "I'll re-shard the index before cutover"),
        _msg("<b@x>", "grace@acme.com", "Staging cutover completed and verified"),
    ]
    thread = _thread(["id-<a@x>", "id-<b@x>"])

    def fake(ctx: ThreadContext):
        return [
            ExtractedEventRaw(
                actor_name_or_email="raj@acme.com",
                type="proposed",
                summary="Will re-shard the index before cutover",
                source_message_id_headers=["<a@x>"],
            ),
            ExtractedEventRaw(
                actor_name_or_email="grace@acme.com",
                type="outcome",
                summary="Staging cutover completed and verified",
                source_message_id_headers=["<b@x>"],
            ),
        ]

    events = extract_events(
        [thread], {"t1": msgs}, [], E2P, PID_OWNER, extract_fn=fake
    )
    by_summary = {e.summary: e.type.value for e in events}
    assert by_summary["Will re-shard the index before cutover"] == "proposed"
    assert by_summary["Staging cutover completed and verified"] == "outcome"


def test_actor_skip_on_unresolvable():
    """An actor that maps to no Person → the event is skipped entirely."""
    msgs = [_msg("<a@x>", "raj@acme.com", "Someone did a thing")]
    thread = _thread(["id-<a@x>"])

    def fake(ctx: ThreadContext):
        return [
            ExtractedEventRaw(
                actor_name_or_email="stranger@external.example",
                type="did",
                summary="Did a thing",
                source_message_id_headers=["<a@x>"],
            )
        ]

    events = extract_events(
        [thread], {"t1": msgs}, [], E2P, PID_OWNER, extract_fn=fake
    )
    assert events == []


def test_source_message_ids_required():
    """An event with no (valid) citation is skipped."""
    msgs = [_msg("<a@x>", "raj@acme.com", "Pushed the branch")]
    thread = _thread(["id-<a@x>"])

    def fake(ctx: ThreadContext):
        return [
            # Empty citations.
            ExtractedEventRaw(
                actor_name_or_email="raj@acme.com",
                type="did",
                summary="Pushed the branch",
                source_message_id_headers=[],
            ),
            # Citation that does not belong to this thread → dropped → skipped.
            ExtractedEventRaw(
                actor_name_or_email="raj@acme.com",
                type="did",
                summary="Pushed the other branch",
                source_message_id_headers=["<not-in-thread@x>"],
            ),
        ]

    events = extract_events(
        [thread], {"t1": msgs}, [], E2P, PID_OWNER, extract_fn=fake
    )
    assert events == []


def test_eval_structural_gates_pass():
    """The Track-A eval's structural gates pass on the shared fixture."""
    from services.enrich.events.eval.run_eval import run

    summary = run(verbose=False)
    assert summary["passed"], summary
