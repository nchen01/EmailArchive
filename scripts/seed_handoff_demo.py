r"""Seed a deterministic HANDOFF DEMO mailbox with L1 Event rows (S17.14).

Why this exists: the Handoff package generator derives claims only from extracted
L1 `Event` rows (no uncited claims, no retrieval hits pulled into evidence). Real
mailboxes like `puluo` have **zero** Event rows because event extraction is the
Anthropic-LLM step (`services/enrich/events_llm.py`), so they cannot exercise the
full publish → recipient → export → versioning flow. This script seeds a small,
purpose-built mailbox with threads + messages + events **directly** — no Gmail,
no LLM, no API key — so the whole Handoff flow can be demoed end to end.

It is isolated from real mailboxes: the owner is `handoff-demo@example.com` (not a
real address), and the script only ever touches ITS OWN mailbox. Idempotent:
re-running clears this demo mailbox's threads/messages/events and re-seeds.

Usage (PowerShell):
    $env:DATABASE_URL='postgresql+psycopg2://ekc:...@localhost:5432/ekc_dev'
    .\.venv\Scripts\python.exe scripts\seed_handoff_demo.py

Then load the printed mailbox_id in the workspace and go to Handoff → Create draft
→ Generate → Publish.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select  # noqa: E402

from services.db import models as orm  # noqa: E402

OWNER_EMAIL = "handoff-demo@example.com"
INTERNAL_DOMAINS = ["acme.com"]
_TS = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def _addresses(display: str) -> dict:
    """Minimal workspace-safe `addresses` blob so evidence shows a display name."""
    return {"sender": {"display_names": [display]}}


# (thread_subject, [messages]) — each message: header, sender, display, body, sensitivity
_THREADS = [
    ("Atlas migration", [
        {"header": "atlas-1@acme.com", "sender": "dana@acme.com", "display": "Dana Ruiz",
         "subject": "Atlas DB cutover", "body": "We completed the Atlas database cutover on Friday. All services are green."},
        {"header": "atlas-2@acme.com", "sender": "dana@acme.com", "display": "Dana Ruiz",
         "subject": "Atlas migration — next steps", "body": "Proposing we migrate the remaining reporting services next sprint."},
    ]),
    ("Contoso renewal", [
        {"header": "contoso-1@contoso.com", "sender": "rep@contoso.com", "display": "Sam Vendor",
         "subject": "Contract renewal", "body": "Confirming the Contoso support contract is renewed for 12 months at the current rate."},
    ]),
    # Whole-thread sensitive: cited by an event but excluded from evidence, so the
    # creator sees the sensitivity gate working (claim dropped, exclusion counted).
    ("Restructuring (confidential)", [
        {"header": "hr-1@acme.com", "sender": "hr@acme.com", "display": "People Team",
         "subject": "Confidential restructuring plan", "body": "Confidential: proposed headcount changes.",
         "sensitivity": ["hr"]},
    ]),
]

# (event_type, summary, [source headers]) — type maps: proposed→open_loop, did/outcome→decision
_EVENTS = [
    ("did", "Completed the Atlas database cutover on Friday", ["atlas-1@acme.com"]),
    ("proposed", "Migrate the remaining reporting services next sprint", ["atlas-2@acme.com"]),
    ("outcome", "Renewed the Contoso support contract for 12 months", ["contoso-1@contoso.com"]),
    ("did", "Finalized the confidential restructuring plan", ["hr-1@acme.com"]),  # → dropped (sensitive)
]


def _get_or_create_mailbox(session) -> tuple[str, str]:
    """Return (mailbox_id, owner_person_id), creating both if needed."""
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
    """Clear THIS demo mailbox's events/messages/threads (child-first) so re-seeding
    is deterministic. Only ever touches the demo mailbox."""
    session.execute(delete(orm.Event).where(orm.Event.mailbox_id == mailbox_id))
    session.execute(delete(orm.Message).where(orm.Message.mailbox_id == mailbox_id))
    session.execute(delete(orm.Thread).where(orm.Thread.mailbox_id == mailbox_id))
    session.commit()


def seed() -> str:
    from services.db.engine import SessionLocal  # delayed: engine reads DATABASE_URL at construction

    session = SessionLocal()
    try:
        mailbox_id, owner_pid = _get_or_create_mailbox(session)
        _reset_content(session, mailbox_id)

        for subject, messages in _THREADS:
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
                    sensitivity=m.get("sensitivity", ["none"]), noise=False,
                ))
        session.commit()

        for etype, summary, headers in _EVENTS:
            session.add(orm.Event(
                mailbox_id=mailbox_id, actor_person_id=owner_pid, type=etype,
                summary=summary, source_message_ids=headers, confidence=0.9,
            ))
        session.commit()

        n_msgs = session.execute(
            select(orm.Message).where(orm.Message.mailbox_id == mailbox_id)
        ).scalars().all()
        n_events = session.execute(
            select(orm.Event).where(orm.Event.mailbox_id == mailbox_id)
        ).scalars().all()
        print(f"Seeded handoff-demo mailbox_id={mailbox_id}")
        print(f"  threads={len(_THREADS)} messages={len(n_msgs)} events={len(n_events)}")
        print("  (1 event cites a whole-thread-sensitive message -> excluded from evidence)")
        print()
        print("Next: load this mailbox_id in the workspace -> Handoff -> Create draft ->")
        print("      Generate -> expect ~3 claims / 3 evidence -> Publish -> copy link.")
        return mailbox_id
    finally:
        session.close()


def main() -> None:
    from scripts._env import load_local_env
    load_local_env()  # load .env before DATABASE_URL is read by db/engine.py
    seed()


if __name__ == "__main__":
    main()
