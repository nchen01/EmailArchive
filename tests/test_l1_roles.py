"""Role inference acceptance gate (spec 01 §5, spec 05 §7).

Gold: fixtures/gold/roles.json  →  person_label → role string.
We join via gold/identities.json  →  email → person_label → role.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def enrichment_result():
    from pathlib import Path

    from services.enrich.params import EnrichParams
    from services.enrich.pipeline import run_enrichment
    from services.ingest.params import IngestParams
    from services.ingest.pipeline import IngestConfig, run_ingest

    mailbox_path = FIXTURE_DIR / "mailbox.json"
    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=mailbox_path,
        owner_email="alex@acme.com",
        internal_domains=["acme.com"],
        params=IngestParams(
            legal_domains=["morrislaw.com"],
            hr_senders=["hr@acme.com"],
        ),
    )
    store = run_ingest(cfg)

    eparams = EnrichParams(legal_domains=["morrislaw.com"])
    return run_enrichment(
        store.messages,
        owner_email="alex@acme.com",
        internal_domains=["acme.com"],
        params=eparams,
    )


@pytest.fixture(scope="module")
def email_to_role(enrichment_result):
    pid_to_role = {p.id: p.role.value for p in enrichment_result.people}
    email_to_pid = {i.email: i.person_id for i in enrichment_result.identities}
    return {email: pid_to_role[pid] for email, pid in email_to_pid.items()}


@pytest.fixture(scope="module")
def gold_email_roles():
    identities = json.loads((FIXTURE_DIR / "gold" / "identities.json").read_text())
    roles      = json.loads((FIXTURE_DIR / "gold" / "roles.json").read_text())
    return {
        email: roles[label]
        for email, label in identities["address_to_person"].items()
    }


def test_roles_match_gold(email_to_role, gold_email_roles):
    """Every address in gold must map to the expected role."""
    failures = []
    for email, expected in gold_email_roles.items():
        got = email_to_role.get(email)
        if got != expected:
            failures.append(f"{email}: expected={expected!r}  got={got!r}")
    assert not failures, "Role mismatches:\n" + "\n".join(failures)


def test_internal_external_split(email_to_role):
    """All acme.com addresses → internal or manager; all others → not internal."""
    for email, role in email_to_role.items():
        domain = email.split("@")[1]
        if domain == "acme.com":
            assert role in ("internal", "manager"), (
                f"{email} is acme.com but got role={role!r}"
            )
        else:
            assert role != "internal", (
                f"{email} is external but got role='internal'"
            )


def test_owner_not_a_contact(enrichment_result):
    """Owner (alex@acme.com) has no Edge — confirmed via graph — and we simply
    verify they resolve to a Person (they are in identities)."""
    owner_pid = next(
        (i.person_id for i in enrichment_result.identities if i.email == "alex@acme.com"),
        None,
    )
    assert owner_pid is not None


def test_role_confidence_nonzero_for_known_roles(enrichment_result, gold_email_roles):
    """Any person with a non-unknown gold role must have confidence > 0."""
    pid_to_person = {p.id: p for p in enrichment_result.people}
    email_to_pid  = {i.email: i.person_id for i in enrichment_result.identities}

    for email, expected_role in gold_email_roles.items():
        if expected_role == "unknown":
            continue
        pid    = email_to_pid.get(email)
        person = pid_to_person.get(pid)
        assert person is not None
        assert person.role_confidence > 0, (
            f"{email}: role={expected_role!r} but confidence=0"
        )
