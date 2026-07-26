r"""Seed a deterministic HANDOFF DEMO mailbox with L1 Event rows (S17.14).

Why this exists: the Handoff package generator derives claims only from extracted
L1 `Event` rows (no uncited claims, no retrieval hits pulled into evidence). Real
mailboxes like `puluo` have **zero** Event rows because event extraction is the
Anthropic-LLM step (`services/enrich/events_llm.py`), so they cannot exercise the
full publish -> recipient -> export -> versioning flow. This script seeds a small,
purpose-built mailbox with threads + messages + events **directly** -- no Gmail,
no LLM, no API key -- so the whole Handoff flow can be demoed end to end.

The smoke dataset is a coherent, non-sensitive engineering handoff: Nexus Auth,
a Connection Pool incident, ML Engineer headcount, and Security Audit
remediation. It also includes one whole-thread-sensitive thread and one noise
(newsletter) message that are BOTH excluded from evidence, so the demo proves the
sensitivity + noise gates work.

It is isolated from real mailboxes: the owner is `handoff-demo@example.com` (not a
real address), and the script only ever touches ITS OWN mailbox. Idempotent:
re-running clears this demo mailbox's threads/messages/events and re-seeds.

Usage (PowerShell):
    $env:DATABASE_URL='postgresql+psycopg2://ekc:...@localhost:5432/ekc_dev'
    .\.venv\Scripts\python.exe scripts\seed_handoff_demo.py

Then load the printed mailbox_id in the workspace and go to Handoff -> Create draft
-> Generate -> Publish.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select  # noqa: E402

from services.db import models as orm  # noqa: E402

OWNER_EMAIL = "handoff-demo@example.com"
INTERNAL_DOMAINS = ["acme.dev"]
_TS = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)

# ── Smoke dataset ────────────────────────────────────────────────────────────
# Each thread: (subject, [message dicts]). A message dict: header, sender,
# display, subject, body, and optional `sensitivity` / `noise` flags. The
# last two threads are DELIBERATELY excluded from evidence (sensitivity + noise).
DEMO_THREADS: list[tuple[str, list[dict]]] = [
    ("Nexus Auth", [
        {"header": "nexus-1@acme.dev", "sender": "dana@acme.dev", "display": "Dana Ruiz",
         "subject": "Nexus Auth SSO cutover",
         "body": "Shipped the Nexus Auth SSO cutover to production. All internal logins now route through Nexus."},
        {"header": "nexus-2@acme.dev", "sender": "dana@acme.dev", "display": "Dana Ruiz",
         "subject": "Nexus Auth - remaining apps",
         "body": "Proposing we migrate the remaining internal apps (wiki, dashboards) to Nexus Auth next sprint."},
    ]),
    ("Connection Pool incident", [
        {"header": "pool-1@acme.dev", "sender": "sre@acme.dev", "display": "SRE On-Call",
         "subject": "INC-482 connection pool exhaustion",
         "body": "Mitigated the connection-pool exhaustion incident by raising the pool ceiling and adding backpressure to the reporting job."},
        {"header": "pool-2@datadoghq.com", "sender": "support@datadoghq.com", "display": "Datadog Support",
         "subject": "INC-482 postmortem inputs",
         "body": "Postmortem inputs: the leaked connections traced back to the nightly reporting job. Dashboards updated."},
    ]),
    ("ML Engineer headcount", [
        {"header": "mlhc-1@acme.dev", "sender": "talent@acme.dev", "display": "Talent Team",
         "subject": "Senior ML Engineer requisition",
         "body": "Opened a requisition for a Senior ML Engineer on the platform team. Awaiting VP sign-off before posting."},
    ]),
    ("Security Audit remediation", [
        {"header": "sec-1@acme.dev", "sender": "security@acme.dev", "display": "Security Team",
         "subject": "SOC2 remediation status",
         "body": "Closed SOC2 remediation items 1 through 7. Evidence attached to the audit tracker."},
        {"header": "sec-2@acme.dev", "sender": "security@acme.dev", "display": "Security Team",
         "subject": "SOC2 remaining items",
         "body": "Two remediation items remain: rotate the service account keys and enable MFA on legacy admin accounts."},
    ]),
    # Whole-thread sensitive: cited by an event but excluded from evidence, so the
    # creator sees the sensitivity gate working (claim dropped, exclusion counted).
    ("Compensation review (confidential)", [
        {"header": "comp-1@acme.dev", "sender": "hr@acme.dev", "display": "People Team",
         "subject": "Confidential Q3 comp review",
         "body": "Confidential: proposed compensation adjustments for Q3.",
         "sensitivity": ["hr"]},
    ]),
    # Noise (newsletter/automation): filtered out of evidence by the noise gate.
    ("Weekly tech digest", [
        {"header": "news-1@techcrunch.com", "sender": "digest@techcrunch.com", "display": "TechCrunch",
         "subject": "Your weekly digest",
         "body": "Top stories in tech this week and a few sponsored picks.",
         "noise": True},
    ]),
]

# (event_type, summary, [source headers]) -- proposed->open_loop; did/outcome->decision.
# The last two cite the sensitive / noise messages and are therefore dropped.
DEMO_EVENTS: list[tuple[str, str, list[str]]] = [
    ("did", "Shipped the Nexus Auth SSO cutover to production", ["nexus-1@acme.dev"]),
    ("proposed", "Migrate the remaining internal apps to Nexus Auth next sprint", ["nexus-2@acme.dev"]),
    ("did", "Mitigated the connection-pool exhaustion incident (INC-482)", ["pool-1@acme.dev"]),
    ("outcome", "Filed the INC-482 postmortem; root cause was a leaked connection", ["pool-2@datadoghq.com"]),
    ("proposed", "Open a Senior ML Engineer requisition (pending VP sign-off)", ["mlhc-1@acme.dev"]),
    ("did", "Closed SOC2 remediation items 1-7", ["sec-1@acme.dev"]),
    ("proposed", "Rotate service keys and enable MFA on legacy admin accounts", ["sec-2@acme.dev"]),
    ("did", "Finalized the confidential Q3 compensation review", ["comp-1@acme.dev"]),  # -> excluded (sensitive)
    ("did", "Skimmed the weekly tech newsletter", ["news-1@techcrunch.com"]),           # -> excluded (noise)
]


def _addresses(display: str) -> dict:
    """Minimal workspace-safe `addresses` blob so evidence shows a display name."""
    return {"sender": {"display_names": [display]}}


def get_or_create_mailbox(session) -> tuple[str, str]:
    """Return (mailbox_id, owner_person_id) for the demo mailbox, creating both."""
    mbx = session.execute(
        select(orm.Mailbox).where(orm.Mailbox.owner_email == OWNER_EMAIL)
    ).scalar_one_or_none()
    if mbx is None:
        mbx = orm.Mailbox(
            provider="gmail", owner_email=OWNER_EMAIL, status="active",
            embed_model="dev-none", embed_dim=0,
            config={"internal_domains": INTERNAL_DOMAINS},
        )
        session.add(mbx)
        session.commit()
    mailbox_id = str(mbx.id)

    owner = session.execute(
        select(orm.Person).where(
            orm.Person.mailbox_id == mailbox_id,
            orm.Person.canonical_email == OWNER_EMAIL,
        )
    ).scalar_one_or_none()
    if owner is None:
        owner = orm.Person(mailbox_id=mailbox_id, canonical_email=OWNER_EMAIL, names=["Handoff Demo"])
        session.add(owner)
        session.commit()
    if mbx.owner_person_id != owner.id:
        mbx.owner_person_id = owner.id
        session.commit()
    return mailbox_id, str(owner.id)


def _reset_content(session, mailbox_id: str) -> None:
    """Clear THIS mailbox's events/messages/threads (child-first) so re-seeding is
    deterministic. Only ever touches the given mailbox."""
    session.execute(delete(orm.Event).where(orm.Event.mailbox_id == mailbox_id))
    session.execute(delete(orm.Message).where(orm.Message.mailbox_id == mailbox_id))
    session.execute(delete(orm.Thread).where(orm.Thread.mailbox_id == mailbox_id))
    session.commit()


def seed_into(session, mailbox_id: str, owner_person_id: str) -> dict:
    """Seed the DEMO_THREADS / DEMO_EVENTS dataset into ``mailbox_id`` (clearing any
    prior demo content first). Reusable by the script and by tests. Returns counts.
    """
    _reset_content(session, mailbox_id)

    for subject, messages in DEMO_THREADS:
        tid = str(uuid.uuid4())
        session.add(orm.Thread(
            id=tid, mailbox_id=mailbox_id, subject_norm=subject, t_start=_TS, t_end=_TS,
        ))
        session.flush()
        for m in messages:
            session.add(orm.Message(
                mailbox_id=mailbox_id, message_id_header=m["header"], provider_id=m["header"],
                thread_id=tid, sender_email=m["sender"], ts=_TS,
                subject=m["subject"], clean_text=m["body"],
                addresses=_addresses(m["display"]),
                sensitivity=m.get("sensitivity", ["none"]), noise=m.get("noise", False),
            ))
    session.commit()

    for etype, summary, headers in DEMO_EVENTS:
        session.add(orm.Event(
            mailbox_id=mailbox_id, actor_person_id=owner_person_id, type=etype,
            summary=summary, source_message_ids=headers, confidence=0.9,
        ))
    session.commit()

    n_msgs = session.execute(
        select(func.count()).select_from(orm.Message).where(orm.Message.mailbox_id == mailbox_id)
    ).scalar_one()
    n_events = session.execute(
        select(func.count()).select_from(orm.Event).where(orm.Event.mailbox_id == mailbox_id)
    ).scalar_one()
    return {"threads": len(DEMO_THREADS), "messages": int(n_msgs), "events": int(n_events)}


def seed() -> str:
    from services.db.engine import SessionLocal  # delayed: engine reads DATABASE_URL at construction

    session = SessionLocal()
    try:
        mailbox_id, owner_pid = get_or_create_mailbox(session)
        counts = seed_into(session, mailbox_id, owner_pid)
        print(f"Seeded handoff-demo mailbox_id={mailbox_id}")
        print(f"  threads={counts['threads']} messages={counts['messages']} events={counts['events']}")
        print("  safe smoke threads: Nexus Auth, Connection Pool incident, ML Engineer")
        print("  headcount, Security Audit remediation")
        print("  excluded: 1 whole-thread-sensitive (comp review) + 1 noise (newsletter)")
        print()
        print("Next: load this mailbox_id in the workspace -> Handoff -> Create draft ->")
        print("      Generate -> expect ~7 claims / 7 evidence -> Publish -> copy link.")
        return mailbox_id
    finally:
        session.close()


def main() -> None:
    from scripts._env import load_local_env
    load_local_env()  # load .env before DATABASE_URL is read by db/engine.py
    seed()


if __name__ == "__main__":
    main()
