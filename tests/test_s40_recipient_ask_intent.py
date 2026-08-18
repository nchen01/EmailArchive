"""S40 - recipient package-local Ask intent shaping (DB-free, pure).

Proves the deterministic ask (services/handoff/ask.py) answers by intent: status vs
next-steps produce different shapes from the SAME package; next-steps returns only
open loops (and an honest "none" when only completed work exists, never restating
it as an action); decisions/blocked filter to their kind; a named project scopes the
answer via the S39 frozen label; and the oracle-safe no-answer path is unchanged.

Pure functions over fake rows (no DB, no Gmail, no LLM), so these are fast and can
never touch the live mailbox.
"""
from __future__ import annotations

from types import SimpleNamespace

from services.handoff.ask import answer_from_package, detect_ask_intent


def _C(cid, kind, text, headers, label=None):
    return SimpleNamespace(id=cid, kind=kind, text=text,
                           source_message_id_headers=headers, project_label=label)


def _E(header, subject="", body="", sender="a", domain="acme.corp"):
    return SimpleNamespace(message_id_header=header, subject=subject, body_snapshot=body,
                           sender_display=sender, sender_domain=domain, ts=None)


# A two-project package: Nexus (1 decision + 1 open loop) and Security (1 open loop).
def _pkg():
    claims = [
        _C("c-nx-d", "decision", "Shipped the Nexus SSO cutover to production",
           ["nx-d@acme.corp"], "Nexus Auth Platform"),
        _C("c-nx-o", "open_loop", "Rotate the remaining Nexus service keys",
           ["nx-o@acme.corp"], "Nexus Auth Platform"),
        _C("c-sec-o", "open_loop", "Finish the Nexus migration for the Security review",
           ["sec-o@acme.corp"], "Security Audit Remediation"),
    ]
    evidence = [
        _E("nx-d@acme.corp", "Nexus SSO", "Nexus cutover shipped to production."),
        _E("nx-o@acme.corp", "Nexus keys", "Rotate remaining Nexus service keys."),
        _E("sec-o@acme.corp", "Security review", "Nexus migration for security review."),
    ]
    return claims, evidence


def test_detect_ask_intent_classifies_each_shape():
    assert detect_ask_intent("What is the status of Nexus Auth Platform?") == "status"
    assert detect_ask_intent("What are the next steps for Nexus?") == "next_steps"
    assert detect_ask_intent("What's blocked on Nexus?") == "blocked"
    assert detect_ask_intent("What decisions were made?") == "decisions"
    assert detect_ask_intent("Tell me about Nexus") == "general"
    # specific wins over status
    assert detect_ask_intent("status of blockers on Nexus") == "blocked"


def test_status_and_next_steps_differ_from_same_package():
    claims, evidence = _pkg()
    status = answer_from_package("What is the status of Nexus Auth Platform?", claims, evidence)
    nxt = answer_from_package("What are the next steps for Nexus Auth Platform?", claims, evidence)

    assert status.answered and nxt.answered
    status_ids = {c.id for c in status.claims}
    next_ids = {c.id for c in nxt.claims}
    # Different shapes: status carries the decision + the open loop; next-steps only
    # the open loop.
    assert status_ids != next_ids
    assert "c-nx-d" in status_ids and "c-nx-o" in status_ids
    assert next_ids == {"c-nx-o"}
    assert all(c.kind == "open_loop" for c in nxt.claims)
    assert status.message != nxt.message


def test_decisions_returns_only_decisions():
    claims, evidence = _pkg()
    r = answer_from_package("What decisions were made on Nexus?", claims, evidence)
    assert r.answered
    assert {c.id for c in r.claims} == {"c-nx-d"}
    assert all(c.kind == "decision" for c in r.claims)


def test_next_steps_scoped_to_named_project_excludes_other_project():
    claims, evidence = _pkg()
    # Both open loops mention "Nexus"/"migration"; naming Nexus must scope out the
    # Security-labelled loop via the frozen project label.
    r = answer_from_package("What are next steps for the Nexus migration?", claims, evidence)
    assert r.answered
    ids = {c.id for c in r.claims}
    assert "c-nx-o" in ids
    assert "c-sec-o" not in ids  # scoped away by project label
    assert "for Nexus Auth Platform" in r.message


def test_next_steps_with_only_completed_work_says_none_and_does_not_restate():
    # A package with only completed (decision) work.
    claims = [_C("c1", "decision", "Shipped the Nexus SSO cutover", ["nx@acme.corp"],
                 "Nexus Auth Platform")]
    evidence = [_E("nx@acme.corp", "Nexus SSO", "Nexus cutover shipped.")]
    r = answer_from_package("What are the next steps for Nexus?", claims, evidence)
    assert r.answered is True          # topic matched (not an oracle miss)
    assert r.claims == []              # no completed work restated as an action
    assert r.evidence == []
    assert "no explicit next steps" in r.message.lower()


def test_blocked_matches_blocker_text_else_none():
    claims = [
        _C("b1", "open_loop", "Blocked on vendor sign-off for the Nexus rollout",
           ["nx-b@acme.corp"], "Nexus Auth Platform"),
        _C("d1", "decision", "Shipped the Nexus cutover", ["nx-d@acme.corp"],
           "Nexus Auth Platform"),
    ]
    evidence = [_E("nx-b@acme.corp", "Nexus vendor", "Blocked on vendor sign-off."),
                _E("nx-d@acme.corp", "Nexus SSO", "Nexus cutover shipped.")]
    hit = answer_from_package("What is blocked on Nexus?", claims, evidence)
    assert hit.answered and {c.id for c in hit.claims} == {"b1"}

    # A package with no blocker-shaped claim -> honest "no blockers found".
    only_done = [_C("d1", "decision", "Shipped the Nexus cutover", ["nx-d@acme.corp"],
                    "Nexus Auth Platform")]
    none = answer_from_package("Anything blocked on Nexus?", only_done,
                               [_E("nx-d@acme.corp", "Nexus SSO", "Nexus cutover shipped.")])
    assert none.answered is True
    assert none.claims == []
    assert "no blockers were found" in none.message.lower()


def test_oracle_safety_unchanged_for_unknown_or_empty_query():
    claims, evidence = _pkg()
    # Unknown/sensitive topic -> neutral no-answer, IDENTICAL regardless of intent.
    for q in ("What are the next steps for payroll layoffs?",
              "status of the compensation planning",
              "what is blocked on the acquisition"):
        r = answer_from_package(q, claims, evidence)
        assert r.answered is False and r.claims == [] and r.evidence == []
    # Empty / stopword-only query -> not answered.
    assert answer_from_package("what is the", claims, evidence).answered is False


def test_every_returned_claim_cites_returned_evidence():
    claims, evidence = _pkg()
    for q in ("status of Nexus", "next steps for Nexus", "decisions on Nexus"):
        r = answer_from_package(q, claims, evidence)
        returned = {e.message_id_header for e in r.evidence}
        for c in r.claims:
            assert any(h in returned for h in c.source_message_id_headers)
