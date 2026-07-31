"""Return handoff creation service (S34, §7/§9/§10).

Creates a `return_delta` package from a published original coverage package and the
coverer's own mailbox: a NEW package + lineage in the coverer's mailbox, its scope
auto-seeded from the original (return_scope.seed_return_scope), and a
`handoff_return_context` row recording provenance + seed method. Mailbox access
derives solely from the coverer owning the source mailbox — the original package
only seeds scope, it never authorizes mailbox access (D15 / §21.1).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select

from services.db import models as orm
from services.handoff.audit import write_handoff_audit
from services.handoff.return_scope import seed_return_scope


def original_recipient_email(session, original_pkg: orm.HandoffPackage) -> str | None:
    r = session.execute(
        select(orm.HandoffRecipient.recipient_email).where(
            orm.HandoffRecipient.package_id == original_pkg.id
        )
    ).scalar_one_or_none()
    return r


def default_return_window(original_pkg: orm.HandoffPackage) -> tuple[date | None, date]:
    """date_from = original published_at date; date_to = today (§9 / §21.3).
    `expires_at` is NEVER used as the coverage-window endpoint."""
    d_from = original_pkg.published_at.date() if original_pkg.published_at else None
    return d_from, datetime.now(timezone.utc).date()


def create_return_draft(
    session,
    *,
    original_pkg: orm.HandoffPackage,
    coverer_mailbox: orm.Mailbox,
    date_from: date | None,
    date_to: date | None,
) -> orm.HandoffPackage:
    """Create the return draft (package + seeded scope + return context). Commits."""
    orig_recipient = original_recipient_email(session, original_pkg) or ""
    seed = seed_return_scope(
        session, original_pkg=original_pkg, coverer_mailbox_id=str(coverer_mailbox.id),
        date_from=date_from, date_to=date_to,
    )

    title = f"Return: {original_pkg.title}".strip() if original_pkg.title else "Return handoff"
    pkg = orm.HandoffPackage(
        mailbox_id=coverer_mailbox.id,
        creator_email=coverer_mailbox.owner_email,
        creator_person_id=coverer_mailbox.owner_person_id,
        status="draft",
        reason="coverage_return",
        title=title,
        package_type="return_delta",
        lineage_id=str(uuid.uuid4()),  # NEW lineage — never shares the original's
    )
    session.add(pkg)
    session.flush()

    session.add(orm.HandoffScope(
        package_id=pkg.id,
        date_from=seed.date_from, date_to=seed.date_to,
        included_project_ids=seed.included_project_ids,
        included_person_ids=seed.included_person_ids,
        allowed_domains=seed.allowed_domains,
    ))
    session.add(orm.HandoffReturnContext(
        package_id=pkg.id,
        original_package_id=original_pkg.id,
        original_lineage_id=original_pkg.lineage_id,
        original_creator_email=original_pkg.creator_email,
        original_recipient_email=orig_recipient,
        return_date_from=seed.date_from, return_date_to=seed.date_to,
        carried_project_ids=seed.carried_project_ids,
        carried_person_ids=seed.carried_person_ids,
        carried_domains=seed.carried_domains,
        carried_area_labels=seed.carried_area_labels,
        seed_method=seed.seed_method,
    ))
    session.commit()

    actor = f"owner:{coverer_mailbox.owner_email}"
    write_handoff_audit(
        session, package_id=pkg.id, lineage_id=pkg.lineage_id, actor=actor,
        action="return_handoff_created",
        metadata={
            "original_package_id": str(original_pkg.id),
            "package_type": "return_delta",
            "seed_method": seed.seed_method,
        },
    )
    write_handoff_audit(
        session, package_id=pkg.id, lineage_id=pkg.lineage_id, actor=actor,
        action="return_scope_seeded",
        metadata={
            "seed_method": seed.seed_method,
            "carried_projects": len(seed.carried_project_ids),
            "carried_areas": len(seed.carried_area_labels),
            "carried_domains": len(seed.carried_domains),
            "resolved_projects": len(seed.included_project_ids),
            "resolved_people": len(seed.included_person_ids),
            "has_date_window": bool(seed.date_from or seed.date_to),
        },
    )
    return pkg
