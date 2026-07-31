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


# ── Coverer (return handoff) dataset ─────────────────────────────────────────
# A SECOND mailbox: the coverage employee (Alex) who covered Dana's Nexus Auth &
# Security Audit areas. Thread subjects share tokens with Dana's coverage areas so
# the return scope-seed resolves them to Alex-side projects (structured). The
# coverage-delta events cover: a decision, a closed open loop, a new open loop, a
# project-state/new-person change, plus one sensitive + one noise item (both inside
# a carried area, so the exclusion gates are demonstrably at work).
COVERER_OWNER_EMAIL = "coverer-demo@example.com"
COVERER_INTERNAL_DOMAINS = ["acme.dev"]

COVERER_THREADS: list[tuple[str, list[dict]]] = [
    ("Nexus Auth key rotation", [
        {"header": "cov-nexus-1@acme.dev", "sender": "coverer-demo@example.com", "display": "Alex Kim",
         "subject": "Rotated Nexus Auth service keys",
         "body": "Rotated the Nexus Auth service account keys and verified every internal app still authenticates."},
    ]),
    ("Security Audit — MFA closure", [
        {"header": "cov-sec-1@acme.dev", "sender": "security@acme.dev", "display": "Security Team",
         "subject": "SOC2 last item closed",
         "body": "Enabled MFA on the legacy admin accounts, closing the last SOC2 remediation item Dana had left open."},
    ]),
    ("Nexus Auth wiki migration", [
        {"header": "cov-wiki-1@acme.dev", "sender": "coverer-demo@example.com", "display": "Alex Kim",
         "subject": "Migrate wiki to Nexus Auth",
         "body": "Proposing we migrate the internal wiki to Nexus Auth next — it is the last app on the old SSO."},
    ]),
    ("Nexus Auth — Northwind SSO", [
        {"header": "cov-partner-1@northwind.example", "sender": "jordan@northwind.example", "display": "Jordan (Northwind)",
         "subject": "Northwind SSO integration",
         "body": "Kicked off the Nexus Auth SSO integration with Northwind; their identity team will send SAML metadata."},
    ]),
    # Whole-thread sensitive, but IN a carried Nexus Auth area → excluded by the gate.
    ("Nexus Auth incident (confidential)", [
        {"header": "cov-sens-1@acme.dev", "sender": "security@acme.dev", "display": "Security Team",
         "subject": "Confidential Nexus Auth incident",
         "body": "Confidential: details of a Nexus Auth security incident under embargo.",
         "sensitivity": ["legal"]},
    ]),
    # Noise, IN a carried Nexus Auth area → filtered by the noise gate.
    ("Nexus Auth ops digest", [
        {"header": "cov-noise-1@statuspage.io", "sender": "notifications@statuspage.io", "display": "Statuspage",
         "subject": "Weekly automated status digest",
         "body": "Automated weekly uptime digest for the Nexus Auth service.",
         "noise": True},
    ]),
]

COVERER_EVENTS: list[tuple[str, str, list[str]]] = [
    ("did", "Rotated the Nexus Auth service account keys", ["cov-nexus-1@acme.dev"]),                       # decision
    ("outcome", "Closed the last SOC2 item — MFA enabled on legacy admin accounts", ["cov-sec-1@acme.dev"]),  # closed loop
    ("proposed", "Migrate the internal wiki to Nexus Auth (last app on old SSO)", ["cov-wiki-1@acme.dev"]),  # new open loop
    ("did", "Kicked off the Northwind SSO integration under Nexus Auth", ["cov-partner-1@northwind.example"]),  # project-state + new person/domain
    ("did", "Handled a confidential Nexus Auth incident", ["cov-sens-1@acme.dev"]),   # excluded (sensitive)
    ("did", "Received the weekly automated status digest", ["cov-noise-1@statuspage.io"]),  # excluded (noise)
]


def _addresses(display: str) -> dict:
    """Minimal workspace-safe `addresses` blob so evidence shows a display name."""
    return {"sender": {"display_names": [display]}}


def _get_or_create_mailbox(session, owner_email: str, internal_domains: list[str], display: str) -> tuple[str, str]:
    """Return (mailbox_id, owner_person_id) for ``owner_email``, creating both."""
    mbx = session.execute(
        select(orm.Mailbox).where(orm.Mailbox.owner_email == owner_email)
    ).scalar_one_or_none()
    if mbx is None:
        mbx = orm.Mailbox(
            provider="gmail", owner_email=owner_email, status="active",
            embed_model="dev-none", embed_dim=0,
            config={"internal_domains": internal_domains},
        )
        session.add(mbx)
        session.commit()
    mailbox_id = str(mbx.id)

    owner = session.execute(
        select(orm.Person).where(
            orm.Person.mailbox_id == mailbox_id,
            orm.Person.canonical_email == owner_email,
        )
    ).scalar_one_or_none()
    if owner is None:
        owner = orm.Person(mailbox_id=mailbox_id, canonical_email=owner_email, names=[display])
        session.add(owner)
        session.commit()
    if mbx.owner_person_id != owner.id:
        mbx.owner_person_id = owner.id
        session.commit()
    return mailbox_id, str(owner.id)


def get_or_create_mailbox(session) -> tuple[str, str]:
    """Return (mailbox_id, owner_person_id) for the original (Dana) demo mailbox."""
    return _get_or_create_mailbox(session, OWNER_EMAIL, INTERNAL_DOMAINS, "Handoff Demo")


def get_or_create_coverer_mailbox(session) -> tuple[str, str]:
    """Return (mailbox_id, owner_person_id) for the coverer (Alex) return mailbox."""
    return _get_or_create_mailbox(session, COVERER_OWNER_EMAIL, COVERER_INTERNAL_DOMAINS, "Coverer Demo")


def _reset_content(session, mailbox_id: str) -> None:
    """Clear THIS mailbox's events/messages/threads (child-first) so re-seeding is
    deterministic. Only ever touches the given mailbox."""
    session.execute(delete(orm.Event).where(orm.Event.mailbox_id == mailbox_id))
    session.execute(delete(orm.Message).where(orm.Message.mailbox_id == mailbox_id))
    session.execute(delete(orm.Thread).where(orm.Thread.mailbox_id == mailbox_id))
    session.commit()


def _reset_projects(session, mailbox_id: str) -> None:
    from sqlalchemy import select as _sel
    proj_ids = _sel(orm.Project.id).where(orm.Project.mailbox_id == mailbox_id)
    session.execute(delete(orm.ThreadProjectAssignment).where(
        orm.ThreadProjectAssignment.project_id.in_(proj_ids)))
    session.execute(delete(orm.Project).where(orm.Project.mailbox_id == mailbox_id))
    session.execute(delete(orm.Identity).where(orm.Identity.mailbox_id == mailbox_id))
    session.commit()


def seed_into(
    session,
    mailbox_id: str,
    owner_person_id: str,
    threads: list[tuple[str, list[dict]]] = DEMO_THREADS,
    events: list[tuple[str, str, list[str]]] = DEMO_EVENTS,
    *,
    with_projects: bool = False,
    ts: datetime = _TS,
) -> dict:
    """Seed the given threads/events dataset into ``mailbox_id`` (clearing any prior
    demo content first). Reusable by the script and by tests. Returns counts.

    ``with_projects`` (demo/return only) also materializes one Project per thread
    (label = subject), assigns the thread, and stamps each event's ``project_id``
    from its first source message's thread — so a return handoff can carry the
    original coverage-area labels and resolve them to the coverer's own projects.
    Default False keeps the plain S17 demo/tests byte-for-byte unchanged.
    """
    _reset_content(session, mailbox_id)
    if with_projects:
        _reset_projects(session, mailbox_id)

    header_to_project: dict[str, str] = {}
    domains: set[str] = set()
    for subject, messages in threads:
        tid = str(uuid.uuid4())
        session.add(orm.Thread(
            id=tid, mailbox_id=mailbox_id, subject_norm=subject, t_start=ts, t_end=ts,
        ))
        session.flush()
        pid = None
        if with_projects:
            proj = orm.Project(mailbox_id=mailbox_id, label=subject, label_source="ctfidf",
                               start=ts, end=ts, confidence=0.9)
            session.add(proj)
            session.flush()
            pid = str(proj.id)
            session.add(orm.ThreadProjectAssignment(
                thread_id=tid, project_id=proj.id, weight=1.0, is_primary=True))
        for m in messages:
            session.add(orm.Message(
                mailbox_id=mailbox_id, message_id_header=m["header"], provider_id=m["header"],
                thread_id=tid, sender_email=m["sender"], ts=ts,
                subject=m["subject"], clean_text=m["body"],
                addresses=_addresses(m["display"]),
                sensitivity=m.get("sensitivity", ["none"]), noise=m.get("noise", False),
            ))
            if pid:
                header_to_project[m["header"]] = pid
            if "@" in m["sender"]:
                domains.add(m["sender"].rsplit("@", 1)[-1].lower())
    session.commit()

    # Identities (for the return scope-seed's domain/person resolution).
    if with_projects:
        seen: set[str] = set()
        for _s, messages in threads:
            for m in messages:
                em = m["sender"].lower()
                if em in seen:
                    continue
                seen.add(em)
                # Reuse an existing Person (e.g. the mailbox owner, or a prior seed)
                # so (mailbox_id, canonical_email) stays unique across re-seeds.
                per = session.execute(
                    select(orm.Person).where(
                        orm.Person.mailbox_id == mailbox_id,
                        orm.Person.canonical_email == em,
                    )
                ).scalar_one_or_none()
                if per is None:
                    per = orm.Person(mailbox_id=mailbox_id, canonical_email=em,
                                     names=[m["display"]], role="internal", role_confidence=0.5)
                    session.add(per)
                    session.flush()
                session.add(orm.Identity(mailbox_id=mailbox_id, email=em,
                                         person_id=per.id, display_names=[m["display"]]))
        session.commit()

    for etype, summary, headers in events:
        session.add(orm.Event(
            mailbox_id=mailbox_id, actor_person_id=owner_person_id, type=etype,
            summary=summary, source_message_ids=headers, confidence=0.9,
            project_id=(header_to_project.get(headers[0]) if with_projects and headers else None),
        ))
    session.commit()

    n_msgs = session.execute(
        select(func.count()).select_from(orm.Message).where(orm.Message.mailbox_id == mailbox_id)
    ).scalar_one()
    n_events = session.execute(
        select(func.count()).select_from(orm.Event).where(orm.Event.mailbox_id == mailbox_id)
    ).scalar_one()
    return {"threads": len(DEMO_THREADS), "messages": int(n_msgs), "events": int(n_events)}


def verify_seed(session, mailbox_id: str, threads: list[tuple[str, list[dict]]] = DEMO_THREADS) -> dict:
    """Dry-run check that the seeded mailbox WOULD generate a good package, with NO
    lasting side effects: generate a throwaway candidate, inspect it, then delete
    the package + its audit rows. Never publishes, never mints a recipient/session/
    capability code. Returns ``{ok, claims, evidence, excluded_ok}``.
    """
    import uuid

    from services.handoff.generator import generate_candidate

    pkg = orm.HandoffPackage(
        mailbox_id=mailbox_id, creator_email=OWNER_EMAIL, reason="vacation",
        lineage_id=str(uuid.uuid4()),
    )
    session.add(pkg)
    session.flush()
    session.add(orm.HandoffScope(package_id=pkg.id))
    session.commit()
    try:
        counts = generate_candidate(session, pkg)
        ev_headers = set(session.execute(
            select(orm.HandoffEvidence.message_id_header)
            .where(orm.HandoffEvidence.package_id == pkg.id)
        ).scalars())
        sensitive = {m["header"] for _s, msgs in threads for m in msgs if m.get("sensitivity")}
        noise = {m["header"] for _s, msgs in threads for m in msgs if m.get("noise")}
        excluded_ok = ev_headers.isdisjoint(sensitive | noise)
        return {
            "ok": counts["claims"] > 0 and counts["evidence"] > 0 and excluded_ok,
            "claims": counts["claims"], "evidence": counts["evidence"],
            "excluded_ok": excluded_ok,
        }
    finally:
        # Leave zero trace: remove the throwaway package (cascades claims/evidence/
        # scope/exclusions) and the candidate_generated audit row (no FK cascade).
        session.execute(delete(orm.HandoffAuditEvent).where(orm.HandoffAuditEvent.package_id == pkg.id))
        session.execute(delete(orm.HandoffPackage).where(orm.HandoffPackage.id == pkg.id))
        session.commit()


def _print_next_steps(mailbox_id: str, counts: dict) -> None:
    print("=" * 68)
    print("  HANDOFF DEMO MAILBOX SEEDED")
    print("=" * 68)
    print(f"  mailbox_id : {mailbox_id}")
    print(f"  seeded     : {counts['threads']} threads / {counts['messages']} messages"
          f" / {counts['events']} events")
    print("  threads    : Nexus Auth, Connection Pool incident, ML Engineer")
    print("               headcount, Security Audit remediation")
    print("  expect     : ~7 claims / ~7 evidence on Generate")
    print("  excluded   : 1 whole-thread-sensitive (comp review) + 1 noise (newsletter)")
    print("-" * 68)
    print("  1. Open   : http://localhost:5173/app")
    print(f"  2. Load   : paste the mailbox_id above into the Mailbox ID box -> Load")
    print("  3. Handoff tab -> Create draft -> Generate -> Publish -> copy link")
    print("-" * 68)
    print("  NOTE: puluo is NOT the full Handoff demo mailbox (it has 0 Event rows);")
    print("        use it for Cover-for-me / Relationship Map only. Handoff generation")
    print("        needs THIS seeded mailbox.")
    print("=" * 68)


def _print_return_steps(dana_id: str, alex_id: str) -> None:
    print("=" * 68)
    print("  RETURN HANDOFF DEMO (two mailboxes)")
    print("=" * 68)
    print(f"  original (Dana / covered)  : {dana_id}   [{OWNER_EMAIL}]")
    print(f"  coverer  (Alex / return)   : {alex_id}   [{COVERER_OWNER_EMAIL}]")
    print("  expect  : original ~7 claims; return ~4 claims (rotation, SOC2 close,")
    print("            wiki migration, Northwind SSO); 1 sensitive + 1 noise excluded")
    print("-" * 68)
    print("  1. Load Dana's mailbox -> Handoff -> Create draft -> Generate ->")
    print(f"     Publish to {COVERER_OWNER_EMAIL}; note the ORIGINAL package id.")
    print("  2. Load Alex's mailbox (paste the coverer id above) -> Handoff ->")
    print("     'Create a return handoff' -> paste the original package id -> Create.")
    print("  3. Coverage areas (Nexus Auth, Security Audit) are preselected; Generate")
    print("     the return -> review 'what changed while Dana was away' -> Publish")
    print("     (recipient defaults to Dana).")
    print("  4. Open the return link: it reads as 'Return handoff / what changed while")
    print("     you were away', package-local Ask still works, no live mailbox links.")
    print("=" * 68)


def seed(verify: bool = False) -> str:
    from services.db.engine import SessionLocal  # delayed: engine reads DATABASE_URL at construction

    session = SessionLocal()
    try:
        mailbox_id, owner_pid = get_or_create_mailbox(session)
        counts = seed_into(session, mailbox_id, owner_pid, with_projects=True)
        _print_next_steps(mailbox_id, counts)

        # Second mailbox: the coverer, for the return-handoff (coverage-delta) demo.
        # Coverer activity must fall inside the return window (original published_at →
        # today), so date it to the START OF TODAY: publishing the original today then
        # creating the return picks it up with the default window (seed + demo same day).
        coverer_ts = datetime.now(timezone.utc).replace(hour=0, minute=5, second=0, microsecond=0)
        alex_id, alex_pid = get_or_create_coverer_mailbox(session)
        cov_counts = seed_into(session, alex_id, alex_pid, COVERER_THREADS, COVERER_EVENTS,
                               with_projects=True, ts=coverer_ts)
        _print_return_steps(mailbox_id, alex_id)

        if verify:
            result = verify_seed(session, mailbox_id)
            status = "OK" if result["ok"] else "FAILED"
            print(f"  verify     : original {status} "
                  f"(claims={result['claims']}, evidence={result['evidence']}, "
                  f"sensitive/noise excluded={result['excluded_ok']})")
            cov = verify_seed(session, alex_id, COVERER_THREADS)
            cov_status = "OK" if cov["ok"] else "FAILED"
            print(f"  verify     : coverer  {cov_status} "
                  f"(claims={cov['claims']}, evidence={cov['evidence']}, "
                  f"sensitive/noise excluded={cov['excluded_ok']}) "
                  f"[no package/token side effects]")
            print("=" * 68)
            if not (result["ok"] and cov["ok"]):
                raise SystemExit("seed verification failed")
        return mailbox_id
    finally:
        session.close()


def main() -> None:
    import argparse

    from scripts._env import load_local_env
    load_local_env()  # load .env before DATABASE_URL is read by db/engine.py

    parser = argparse.ArgumentParser(description="Seed the deterministic Handoff demo mailbox.")
    parser.add_argument(
        "--verify", action="store_true",
        help="after seeding, dry-run a generate to confirm non-empty claims/evidence "
             "and sensitive/noise exclusion (no publish/token side effects)",
    )
    args = parser.parse_args()
    seed(verify=args.verify)


if __name__ == "__main__":
    main()
