r"""Seed a richer deterministic handoff demo mailbox.

The small ``scripts/seed_handoff_demo.py`` fixture is intentionally tiny and
fast. This script creates a larger, more realistic mailbox for product demos:
12 projects, 72 threads, and 288 messages by default, with grounded Event rows,
Projects, ThreadProjectAssignments, ProjectMembers, People/Identities, Orgs, and
Edges. It does not call Gmail, Voyage, Anthropic, or any external API.

The generated subjects are deliberately realistic thread subjects rather than
bare project names. The project name appears naturally in every message body so
handoff generation, project view, and search-oriented demos have coherent text.

Usage (PowerShell):
    $env:DATABASE_URL='postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev'
    .\.venv\Scripts\python.exe scripts\seed_rich_handoff_demo.py --verify
"""
from __future__ import annotations

import argparse
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select  # noqa: E402

from services.db import models as orm  # noqa: E402

OWNER_EMAIL = "rich-handoff-demo@example.com"
OWNER_DISPLAY = "Dana Ruiz"
INTERNAL_DOMAINS = ["acme.dev"]
BASE_TS = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
DEFAULT_THREADS_PER_PROJECT = 6


@dataclass(frozen=True)
class Contact:
    email: str
    name: str
    role: str = "unknown"


@dataclass(frozen=True)
class ProjectSpec:
    label: str
    slug: str
    owner: Contact
    contacts: tuple[Contact, ...]
    topics: tuple[str, ...]
    external_domain: str | None = None


PROJECTS: tuple[ProjectSpec, ...] = (
    ProjectSpec(
        label="Nexus Auth Platform",
        slug="nexus-auth",
        owner=Contact("mira@acme.dev", "Mira Patel", "lead"),
        contacts=(
            Contact("security@acme.dev", "Security Team", "security"),
            Contact("idp-support@okta.example", "Okta Support", "vendor"),
            Contact("qa-platform@acme.dev", "Platform QA", "qa"),
        ),
        topics=(
            "wiki SSO cutover timing",
            "service account key rotation",
            "legacy admin MFA rollout",
            "SAML metadata handoff",
            "dashboard login regression",
            "session timeout policy",
        ),
        external_domain="okta.example",
    ),
    ProjectSpec(
        label="Security Audit Remediation",
        slug="soc2-remediation",
        owner=Contact("security@acme.dev", "Security Team", "security"),
        contacts=(
            Contact("compliance@acme.dev", "Compliance Team", "compliance"),
            Contact("mira@acme.dev", "Mira Patel", "lead"),
            Contact("auditor@presidio.example", "Presidio Audit", "auditor"),
        ),
        topics=(
            "control evidence upload",
            "legacy MFA exception list",
            "access review sign-off",
            "vendor evidence packet",
            "key rotation proof",
            "policy owner mapping",
        ),
        external_domain="presidio.example",
    ),
    ProjectSpec(
        label="Harbor Billing Migration",
        slug="harbor-billing",
        owner=Contact("finance-eng@acme.dev", "Finance Engineering", "lead"),
        contacts=(
            Contact("billing-ops@acme.dev", "Billing Ops", "ops"),
            Contact("revops@acme.dev", "Revenue Ops", "ops"),
            Contact("support@stripe.example", "Stripe Support", "vendor"),
        ),
        topics=(
            "invoice preview mismatch",
            "retry schedule for failed cards",
            "usage export reconciliation",
            "tax code mapping",
            "contract renewal proration",
            "customer credit memo queue",
        ),
        external_domain="stripe.example",
    ),
    ProjectSpec(
        label="Atlas Data Pipeline",
        slug="atlas-pipeline",
        owner=Contact("data-eng@acme.dev", "Data Engineering", "lead"),
        contacts=(
            Contact("analytics@acme.dev", "Analytics", "analyst"),
            Contact("sre@acme.dev", "SRE On-Call", "ops"),
            Contact("support@datadoghq.com", "Datadog Support", "vendor"),
        ),
        topics=(
            "late-arriving events backfill",
            "warehouse load retry",
            "dashboard freshness alert",
            "schema drift in account table",
            "daily export handoff",
            "pipeline ownership cleanup",
        ),
        external_domain="datadoghq.com",
    ),
    ProjectSpec(
        label="Mobile Checkout Reliability",
        slug="mobile-checkout",
        owner=Contact("mobile@acme.dev", "Mobile Team", "lead"),
        contacts=(
            Contact("payments@acme.dev", "Payments Team", "engineer"),
            Contact("support@acme.dev", "Customer Support", "support"),
            Contact("qa-mobile@acme.dev", "Mobile QA", "qa"),
        ),
        topics=(
            "iOS retry loop after auth",
            "Android wallet timeout",
            "checkout crash triage",
            "payment sheet copy review",
            "release candidate hold",
            "refund webhook follow-up",
        ),
    ),
    ProjectSpec(
        label="Northwind SSO Integration",
        slug="northwind-sso",
        owner=Contact("partners@acme.dev", "Partner Engineering", "lead"),
        contacts=(
            Contact("jordan@northwind.example", "Jordan Lee", "partner"),
            Contact("identity@northwind.example", "Northwind Identity", "partner"),
            Contact("mira@acme.dev", "Mira Patel", "lead"),
        ),
        topics=(
            "SAML metadata exchange",
            "staging tenant smoke test",
            "attribute mapping question",
            "partner launch checklist",
            "support escalation path",
            "production certificate rollover",
        ),
        external_domain="northwind.example",
    ),
    ProjectSpec(
        label="Customer Escalation Queue",
        slug="customer-escalations",
        owner=Contact("support@acme.dev", "Customer Support", "support"),
        contacts=(
            Contact("success@acme.dev", "Customer Success", "support"),
            Contact("product@acme.dev", "Product Team", "product"),
            Contact("samira@contoso.example", "Samira at Contoso", "customer"),
        ),
        topics=(
            "Contoso renewal blocker",
            "enterprise SLA response",
            "escalation owner rotation",
            "bug triage for export flow",
            "customer comms draft",
            "support macro cleanup",
        ),
        external_domain="contoso.example",
    ),
    ProjectSpec(
        label="Search Relevance Tuning",
        slug="search-relevance",
        owner=Contact("ml-platform@acme.dev", "ML Platform", "lead"),
        contacts=(
            Contact("search@acme.dev", "Search Team", "engineer"),
            Contact("evals@acme.dev", "Eval Team", "analyst"),
            Contact("product@acme.dev", "Product Team", "product"),
        ),
        topics=(
            "ranking eval miss",
            "query rewrite guardrail",
            "embedding drift sample",
            "recall threshold review",
            "search result explanation",
            "launch criteria for tuning",
        ),
    ),
    ProjectSpec(
        label="Datadog Cost Controls",
        slug="datadog-cost",
        owner=Contact("sre@acme.dev", "SRE On-Call", "ops"),
        contacts=(
            Contact("finops@acme.dev", "FinOps", "ops"),
            Contact("observability@acme.dev", "Observability Team", "engineer"),
            Contact("support@datadoghq.com", "Datadog Support", "vendor"),
        ),
        topics=(
            "log retention policy",
            "custom metric cardinality",
            "dashboard owner cleanup",
            "usage anomaly review",
            "ingest sampling rollout",
            "contract true-up forecast",
        ),
        external_domain="datadoghq.com",
    ),
    ProjectSpec(
        label="Partner API Launch",
        slug="partner-api",
        owner=Contact("api-platform@acme.dev", "API Platform", "lead"),
        contacts=(
            Contact("partners@acme.dev", "Partner Engineering", "lead"),
            Contact("docs@acme.dev", "Developer Docs", "docs"),
            Contact("liam@globex.example", "Liam at Globex", "partner"),
        ),
        topics=(
            "sandbox rate limit",
            "developer docs review",
            "webhook retry contract",
            "beta partner onboarding",
            "API key rotation notice",
            "launch readiness checklist",
        ),
        external_domain="globex.example",
    ),
    ProjectSpec(
        label="Incident Runbook Refresh",
        slug="incident-runbooks",
        owner=Contact("sre@acme.dev", "SRE On-Call", "ops"),
        contacts=(
            Contact("security@acme.dev", "Security Team", "security"),
            Contact("support@acme.dev", "Customer Support", "support"),
            Contact("engineering-managers@acme.dev", "Engineering Managers", "manager"),
        ),
        topics=(
            "on-call escalation matrix",
            "database failover drill",
            "customer comms template",
            "incident severity labels",
            "postmortem owner handoff",
            "runbook review meeting",
        ),
    ),
    ProjectSpec(
        label="ML Hiring Plan",
        slug="ml-hiring",
        owner=Contact("talent@acme.dev", "Talent Team", "recruiting"),
        contacts=(
            Contact("ml-platform@acme.dev", "ML Platform", "lead"),
            Contact("finance@acme.dev", "Finance Team", "finance"),
            Contact("recruiting@acme.dev", "Recruiting Ops", "recruiting"),
        ),
        topics=(
            "senior ML engineer requisition",
            "interview loop calibration",
            "headcount approval timing",
            "candidate slate review",
            "comp band exception",
            "take-home exercise refresh",
        ),
    ),
)

ACTION_BY_INDEX = (
    ("did", "Decided the next step for {topic} in {project}"),
    ("proposed", "Follow up on {topic} for {project}"),
    ("outcome", "Closed the loop on {topic} for {project}"),
)

SUBJECT_PREFIXES = (
    "Follow-up from Tuesday's review",
    "Can we lock the owner before EOD?",
    "Decision notes from the working session",
    "Quick check before the cutover",
    "Draft plan for next week's handoff",
    "Question from the partner thread",
    "Status before tomorrow's standup",
    "Need a call on the rollout order",
)

BODY_OPENERS = (
    "Capturing the latest thread so coverage is straightforward.",
    "Following up from the working session.",
    "Adding the current status before the handoff window.",
    "Writing this down so the next owner has the full context.",
)

BODY_DETAILS = (
    "The blocker is narrow, but the owner should check the dependency before committing the date.",
    "The decision is safe to proceed if the checklist item below is completed.",
    "The customer-facing copy is ready, but the operational owner still needs to confirm timing.",
    "The remaining work is mostly coordination, not a new technical design.",
    "The external team is waiting for a clear answer and can proceed once we send the artifact.",
    "The test pass is clean enough for staging, but production should wait for one more sign-off.",
)


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")


def _addr(c: Contact) -> dict:
    # Full address shape (raw + email + display_names) so mappers.row_to_message
    # parses it without a KeyError — matches what real ingestion produces.
    return {"raw": f"{c.name} <{c.email}>", "email": c.email, "display_names": [c.name]}


def _addresses(sender: Contact, to_contacts: list[Contact], cc_contacts: list[Contact]) -> dict:
    return {
        "sender": _addr(sender),
        "to": [_addr(c) for c in to_contacts],
        "cc": [_addr(c) for c in cc_contacts],
    }


def build_rich_dataset(threads_per_project: int = DEFAULT_THREADS_PER_PROJECT) -> tuple[list[dict], list[dict]]:
    """Return deterministic thread and event dictionaries for the rich demo.

    Default size: 12 projects * 6 threads/project * 4 messages/thread = 288
    messages. Thread-level sensitive/noise cases are included and cited by events
    so the generator demonstrates exclusion gates.
    """
    if threads_per_project < 1:
        raise ValueError("threads_per_project must be positive")

    threads: list[dict] = []
    events: list[dict] = []
    owner = Contact(OWNER_EMAIL, OWNER_DISPLAY, "owner")
    message_total = 0

    for p_idx, project in enumerate(PROJECTS):
        for t_idx in range(threads_per_project):
            topic = project.topics[t_idx % len(project.topics)]
            topic_slug = _slug(topic)
            day_offset = p_idx * 4 + t_idx
            ts = BASE_TS + timedelta(days=day_offset)
            subject = f"{SUBJECT_PREFIXES[(p_idx + t_idx) % len(SUBJECT_PREFIXES)]}: {topic}"
            thread_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"rich-thread:{project.slug}:{t_idx}"))

            is_sensitive = (p_idx, t_idx) in {(1, 4), (11, 4)}
            is_noise = (p_idx, t_idx) in {(8, 5), (10, 5)}
            messages: list[dict] = []
            contact_cycle = [project.owner, *project.contacts]
            message_count = 4

            for m_idx in range(message_count):
                sender = owner if m_idx in {0, 3} else contact_cycle[(m_idx + t_idx) % len(contact_cycle)]
                to_contacts = [project.owner] if sender.email == OWNER_EMAIL else [owner]
                cc_contacts = [c for c in project.contacts[:2] if c.email != sender.email][:1]
                header = f"{project.slug}-t{t_idx + 1}-m{m_idx + 1}@rich.demo"
                msg_subject = subject if m_idx == 0 else f"Re: {subject}"
                if m_idx == 2:
                    msg_subject = f"Confirming the {topic} owner"
                if is_noise:
                    msg_subject = f"Automated digest for {topic}"
                if is_sensitive:
                    msg_subject = f"Confidential review notes for {topic}"
                body = (
                    f"{BODY_OPENERS[(m_idx + p_idx) % len(BODY_OPENERS)]} "
                    f"For {project.label}, the current thread is about {topic}. "
                    f"{BODY_DETAILS[(m_idx + t_idx) % len(BODY_DETAILS)]} "
                    f"Please keep {project.label} in the handoff notes because this "
                    f"affects the coverage plan, the next owner, and the evidence trail."
                )
                if m_idx == 1:
                    body += f" {sender.name} is the best person to ask about this part of {project.label}."
                if project.external_domain and m_idx == 2:
                    body += f" The external dependency is with {project.external_domain}, and they need a concise update."
                if is_sensitive:
                    body += " Confidential personnel/legal context is intentionally included so the sensitivity gate excludes this thread."
                if is_noise:
                    body += " This automated notification is intentionally marked as noise and should not become handoff evidence."

                messages.append({
                    "header": header,
                    "provider_id": f"rich-{header}",
                    "sender": sender.email,
                    "sender_display": sender.name,
                    "to": [c.email for c in to_contacts],
                    "cc": [c.email for c in cc_contacts],
                    "addresses": _addresses(sender, to_contacts, cc_contacts),
                    "subject": msg_subject,
                    "body": body,
                    "ts": ts + timedelta(minutes=17 * m_idx),
                    "sensitivity": ["legal"] if is_sensitive else ["none"],
                    "noise": is_noise,
                })

            event_type, event_tmpl = ACTION_BY_INDEX[(p_idx + t_idx) % len(ACTION_BY_INDEX)]
            source = messages[-1]["header"]
            events.append({
                "type": event_type,
                "summary": event_tmpl.format(topic=topic, project=project.label),
                "source_message_ids": [source],
                "project_slug": project.slug,
            })
            threads.append({
                "id": thread_id,
                "project": project,
                "topic": topic,
                "subject": subject,
                "messages": messages,
                "sensitivity": is_sensitive,
                "noise": is_noise,
            })
            message_total += message_count

    if not 200 <= message_total <= 500:
        raise ValueError(f"rich dataset generated {message_total} messages; expected 200-500")
    return threads, events


def _get_or_create_mailbox(session) -> tuple[str, str]:
    mbx = session.execute(
        select(orm.Mailbox).where(orm.Mailbox.owner_email == OWNER_EMAIL)
    ).scalar_one_or_none()
    if mbx is None:
        mbx = orm.Mailbox(
            provider="gmail",
            owner_email=OWNER_EMAIL,
            status="active",
            embed_model="dev-none",
            embed_dim=0,
            config={"internal_domains": INTERNAL_DOMAINS, "demo_profile": "rich_handoff"},
        )
        session.add(mbx)
        session.commit()

    owner = session.execute(
        select(orm.Person).where(
            orm.Person.mailbox_id == str(mbx.id),
            orm.Person.canonical_email == OWNER_EMAIL,
        )
    ).scalar_one_or_none()
    if owner is None:
        owner = orm.Person(
            mailbox_id=str(mbx.id),
            canonical_email=OWNER_EMAIL,
            names=[OWNER_DISPLAY],
            role="internal",
            role_confidence=1.0,
        )
        session.add(owner)
        session.commit()
    if mbx.owner_person_id != owner.id:
        mbx.owner_person_id = owner.id
        session.commit()
    return str(mbx.id), str(owner.id)


def _reset_rich_mailbox(session, mailbox_id: str, owner_person_id: str) -> None:
    pkg_ids = select(orm.HandoffPackage.id).where(orm.HandoffPackage.mailbox_id == mailbox_id)
    proj_ids = select(orm.Project.id).where(orm.Project.mailbox_id == mailbox_id)

    session.execute(delete(orm.HandoffAuditEvent).where(orm.HandoffAuditEvent.package_id.in_(pkg_ids)))
    session.execute(delete(orm.HandoffPackage).where(orm.HandoffPackage.mailbox_id == mailbox_id))
    session.execute(delete(orm.Event).where(orm.Event.mailbox_id == mailbox_id))
    session.execute(delete(orm.Message).where(orm.Message.mailbox_id == mailbox_id))
    session.execute(delete(orm.Thread).where(orm.Thread.mailbox_id == mailbox_id))
    session.execute(delete(orm.ProjectMember).where(orm.ProjectMember.project_id.in_(proj_ids)))
    session.execute(delete(orm.ThreadProjectAssignment).where(orm.ThreadProjectAssignment.project_id.in_(proj_ids)))
    session.execute(delete(orm.Project).where(orm.Project.mailbox_id == mailbox_id))
    session.execute(delete(orm.Edge).where(orm.Edge.mailbox_id == mailbox_id))
    session.execute(delete(orm.Identity).where(orm.Identity.mailbox_id == mailbox_id))
    session.execute(delete(orm.Person).where(
        orm.Person.mailbox_id == mailbox_id,
        orm.Person.id != owner_person_id,
    ))
    session.execute(delete(orm.Org).where(orm.Org.mailbox_id == mailbox_id))
    session.commit()


def _org_for_domain(session, mailbox_id: str, domain: str, cache: dict[str, str]) -> str:
    if domain in cache:
        return cache[domain]
    name = "Acme" if domain == "acme.dev" else domain.split(".", 1)[0].title()
    org = orm.Org(mailbox_id=mailbox_id, name=name, domains=[domain], internal=domain in INTERNAL_DOMAINS)
    session.add(org)
    session.flush()
    cache[domain] = str(org.id)
    return str(org.id)


def _upsert_person(
    session,
    mailbox_id: str,
    contact: Contact,
    org_cache: dict[str, str],
    person_cache: dict[str, str],
) -> str:
    email = contact.email.lower()
    if email in person_cache:
        return person_cache[email]
    domain = email.rsplit("@", 1)[-1]
    org_id = _org_for_domain(session, mailbox_id, domain, org_cache)
    person = session.execute(
        select(orm.Person).where(
            orm.Person.mailbox_id == mailbox_id,
            orm.Person.canonical_email == email,
        )
    ).scalar_one_or_none()
    if person is None:
        person = orm.Person(
            mailbox_id=mailbox_id,
            canonical_email=email,
            names=[contact.name],
            org_id=org_id,
            role=_schema_role(contact.role),
            role_confidence=0.8 if contact.role != "unknown" else 0.4,
        )
        session.add(person)
        session.flush()
    session.add(orm.Identity(mailbox_id=mailbox_id, email=email, person_id=person.id, display_names=[contact.name]))
    person_cache[email] = str(person.id)
    return str(person.id)


def _schema_role(role: str) -> str:
    if role in {"account_exec", "lead", "internal", "manager", "vendor", "unknown"}:
        return role
    if role in {"customer", "partner"}:
        return "account_exec"
    if role in {
        "owner", "engineer", "security", "compliance", "qa", "ops", "analyst",
        "product", "support", "docs", "finance", "recruiting",
    }:
        return "internal"
    return "unknown"


def seed_rich_mailbox(session, threads_per_project: int = DEFAULT_THREADS_PER_PROJECT) -> dict:
    mailbox_id, owner_person_id = _get_or_create_mailbox(session)
    _reset_rich_mailbox(session, mailbox_id, owner_person_id)
    threads, events = build_rich_dataset(threads_per_project)

    org_cache: dict[str, str] = {}
    person_cache: dict[str, str] = {OWNER_EMAIL: owner_person_id}
    _upsert_person(session, mailbox_id, Contact(OWNER_EMAIL, OWNER_DISPLAY, "owner"), org_cache, person_cache)

    project_by_slug: dict[str, str] = {}
    header_to_project: dict[str, str] = {}
    header_to_thread: dict[str, str] = {}
    person_msg_counts: Counter[str] = Counter()
    project_person_counts: dict[str, Counter[str]] = defaultdict(Counter)
    first_last_by_person: dict[str, list[datetime]] = defaultdict(list)

    for project in PROJECTS:
        proj_threads = [t for t in threads if t["project"].slug == project.slug]
        start = min(m["ts"] for t in proj_threads for m in t["messages"])
        end = max(m["ts"] for t in proj_threads for m in t["messages"])
        proj = orm.Project(
            mailbox_id=mailbox_id,
            label=project.label,
            label_source="user",
            start=start,
            end=end,
            confidence=0.95,
            debug={"seed": "rich_handoff", "slug": project.slug},
        )
        session.add(proj)
        session.flush()
        project_by_slug[project.slug] = str(proj.id)

    session.commit()

    for t in threads:
        project = t["project"]
        project_id = project_by_slug[project.slug]
        all_participants = sorted({
            OWNER_EMAIL,
            *(m["sender"].lower() for m in t["messages"]),
            *(email.lower() for m in t["messages"] for email in (m["to"] + m["cc"])),
        })
        thread = orm.Thread(
            id=t["id"],
            mailbox_id=mailbox_id,
            root_message_id_header=t["messages"][0]["header"],
            subject_norm=t["subject"],
            participants=all_participants,
            t_start=min(m["ts"] for m in t["messages"]),
            t_end=max(m["ts"] for m in t["messages"]),
        )
        session.add(thread)
        session.add(orm.ThreadProjectAssignment(
            thread_id=t["id"], project_id=project_id, weight=1.0, is_primary=True
        ))
        session.flush()

        for m in t["messages"]:
            sender_contact = _contact_for_email(project, m["sender"], fallback_name=m["sender_display"])
            sender_pid = _upsert_person(session, mailbox_id, sender_contact, org_cache, person_cache)
            person_msg_counts[sender_pid] += 1
            project_person_counts[project_id][sender_pid] += 1
            first_last_by_person[sender_pid].append(m["ts"])
            for email in m["to"] + m["cc"]:
                contact = _contact_for_email(project, email, fallback_name=email.split("@", 1)[0].title())
                pid = _upsert_person(session, mailbox_id, contact, org_cache, person_cache)
                project_person_counts[project_id][pid] += 1
                first_last_by_person[pid].append(m["ts"])

            session.add(orm.Message(
                mailbox_id=mailbox_id,
                message_id_header=m["header"],
                provider_id=m["provider_id"],
                thread_id=t["id"],
                sender_email=m["sender"].lower(),
                to_emails=[e.lower() for e in m["to"]],
                cc_emails=[e.lower() for e in m["cc"]],
                addresses=m["addresses"],
                ts=m["ts"],
                subject=m["subject"],
                clean_text=m["body"],
                link_domains=[],
                sensitivity=m["sensitivity"],
                noise=m["noise"],
            ))
            header_to_project[m["header"]] = project_id
            header_to_thread[m["header"]] = t["id"]

    session.commit()

    for project_id, counts in project_person_counts.items():
        total = max(sum(counts.values()), 1)
        for person_id, count in counts.items():
            if person_id == owner_person_id:
                continue
            session.add(orm.ProjectMember(
                project_id=project_id,
                person_id=person_id,
                involvement=round(count / total, 4),
                message_count=count,
            ))

    for person_id, dates in first_last_by_person.items():
        if person_id == owner_person_id:
            continue
        msg_count = person_msg_counts.get(person_id, len(dates))
        session.add(orm.Edge(
            mailbox_id=mailbox_id,
            person_id=person_id,
            message_count=msg_count,
            sent_to_count=max(1, msg_count // 2),
            received_count=max(1, msg_count - (msg_count // 2)),
            first_contact=min(dates),
            last_contact=max(dates),
            weight=round(1.0 + min(msg_count, 12) / 4, 4),
        ))

    for ev in events:
        session.add(orm.Event(
            mailbox_id=mailbox_id,
            actor_person_id=owner_person_id,
            type=ev["type"],
            summary=ev["summary"],
            source_message_ids=ev["source_message_ids"],
            confidence=0.9,
            project_id=header_to_project[ev["source_message_ids"][0]],
        ))
        # Keep pyright/mypy-minded readers happy: this map is deliberately built
        # so tests can assert every event cites an existing thread if needed.
        _ = header_to_thread[ev["source_message_ids"][0]]

    session.commit()
    return rich_counts(session, mailbox_id)


def _contact_for_email(project: ProjectSpec, email: str, fallback_name: str) -> Contact:
    email = email.lower()
    if email == OWNER_EMAIL:
        return Contact(OWNER_EMAIL, OWNER_DISPLAY, "owner")
    for contact in (project.owner, *project.contacts):
        if contact.email.lower() == email:
            return contact
    return Contact(email, fallback_name, "unknown")


def rich_counts(session, mailbox_id: str) -> dict:
    def count(model) -> int:
        return int(session.execute(
            select(func.count()).select_from(model).where(model.mailbox_id == mailbox_id)
        ).scalar_one())

    return {
        "mailbox_id": mailbox_id,
        "projects": count(orm.Project),
        "threads": count(orm.Thread),
        "messages": count(orm.Message),
        "events": count(orm.Event),
        "people": count(orm.Person),
        "edges": count(orm.Edge),
    }


def verify_rich_mailbox(session, mailbox_id: str) -> dict:
    from services.handoff.generator import generate_candidate

    pkg = orm.HandoffPackage(
        mailbox_id=mailbox_id,
        creator_email=OWNER_EMAIL,
        reason="vacation",
        title="Rich handoff demo verification",
        lineage_id=str(uuid.uuid4()),
    )
    session.add(pkg)
    session.flush()
    session.add(orm.HandoffScope(package_id=pkg.id))
    session.commit()
    try:
        counts = generate_candidate(session, pkg)
        evidence_headers = set(session.execute(
            select(orm.HandoffEvidence.message_id_header).where(orm.HandoffEvidence.package_id == pkg.id)
        ).scalars())
        excluded_headers = set(session.execute(
            select(orm.Message.message_id_header).where(
                orm.Message.mailbox_id == mailbox_id,
                (orm.Message.noise == True) | (orm.Message.sensitivity != ["none"]),  # noqa: E712
            )
        ).scalars())
        ok = (
            counts["claims"] >= 50
            and counts["evidence"] >= 50
            and evidence_headers.isdisjoint(excluded_headers)
        )
        return {
            "ok": ok,
            "claims": counts["claims"],
            "evidence": counts["evidence"],
            "excluded_ok": evidence_headers.isdisjoint(excluded_headers),
        }
    finally:
        session.execute(delete(orm.HandoffAuditEvent).where(orm.HandoffAuditEvent.package_id == pkg.id))
        session.execute(delete(orm.HandoffPackage).where(orm.HandoffPackage.id == pkg.id))
        session.commit()


def _print_summary(counts: dict, verify: dict | None = None) -> None:
    print("=" * 72)
    print("  RICH HANDOFF DEMO MAILBOX SEEDED")
    print("=" * 72)
    print(f"  mailbox_id : {counts['mailbox_id']}")
    print(f"  owner      : {OWNER_EMAIL}")
    print(f"  seeded     : {counts['projects']} projects / {counts['threads']} threads / "
          f"{counts['messages']} messages / {counts['events']} events")
    print(f"  people     : {counts['people']} people / {counts['edges']} relationship edges")
    print("  projects   : Nexus Auth Platform, Security Audit Remediation, Harbor")
    print("               Billing Migration, Atlas Data Pipeline, Mobile Checkout,")
    print("               Northwind SSO, Search Relevance, Partner API, and more")
    print("-" * 72)
    print("  1. Open    : http://localhost:5173/app")
    print("  2. Load    : paste the mailbox_id above into the Mailbox ID box -> Load")
    print("  3. Explore : Projects, Relationship Map, Cover-for-me, Handoff")
    print("  4. Handoff : Create draft -> optionally scope to projects -> Generate")
    print("-" * 72)
    print("  NOTE: This rich mailbox is for a denser investor/product demo. It is")
    print("        isolated from puluo and from the small handoff-demo mailbox.")
    if verify:
        status = "OK" if verify["ok"] else "FAILED"
        print(f"  verify    : {status} (claims={verify['claims']}, evidence={verify['evidence']}, "
              f"sensitive/noise excluded={verify['excluded_ok']}) [no package/token side effects]")
    print("=" * 72)


def seed(threads_per_project: int = DEFAULT_THREADS_PER_PROJECT, verify: bool = False) -> str:
    from services.db.engine import SessionLocal

    session = SessionLocal()
    try:
        counts = seed_rich_mailbox(session, threads_per_project=threads_per_project)
        verification = verify_rich_mailbox(session, counts["mailbox_id"]) if verify else None
        _print_summary(counts, verification)
        if verification and not verification["ok"]:
            raise SystemExit("rich seed verification failed")
        return counts["mailbox_id"]
    finally:
        session.close()


def main() -> None:
    from scripts._env import load_local_env

    load_local_env()
    parser = argparse.ArgumentParser(description="Seed a richer deterministic handoff demo mailbox.")
    parser.add_argument(
        "--threads-per-project",
        type=int,
        default=DEFAULT_THREADS_PER_PROJECT,
        help="default 6 -> 72 threads and 288 messages; keep total messages between 200 and 500",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="dry-run a handoff generation after seeding; deletes the throwaway package afterward",
    )
    args = parser.parse_args()
    seed(threads_per_project=args.threads_per_project, verify=args.verify)


if __name__ == "__main__":
    main()
