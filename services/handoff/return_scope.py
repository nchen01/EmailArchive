"""Return-handoff scope seed (S34, docs/s33-return-handoff-coverage-delta-plan.md §8, §12).

Builds the return draft's scope automatically from the ORIGINAL coverage package,
then resolves it against the COVERER's mailbox. The hard rule (§12): project ids
are mailbox-local, so the original mailbox's `project_id`s are carried only as
**provenance** and are never used as filters against the coverer's mailbox.
Resolution is tiered:

  1. Structured — match the original coverage-area *labels* to the coverer's own
     materialized Project labels (token overlap) → coverer-side project ids.
  2. Snapshot hints — resolve carried people (by email) and carried domains to the
     coverer's own Identities → coverer-side person ids.

Exactly one of the two resolved filters is applied to the return scope (project
ids preferred) so the generator's AND-composition never over-restricts; the other
carried descriptors are recorded in `handoff_return_context` for audit/provenance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select

from services.db import models as orm

_STOP = {
    "the", "and", "for", "with", "team", "project", "work", "misc", "general",
    "other", "into", "from", "about", "notes", "update", "updates",
}


def _tokens(label: str | None) -> set[str]:
    """Lowercased alnum tokens (len>=3, non-stopword) for coarse label matching."""
    if not label:
        return set()
    return {t for t in re.split(r"[^a-z0-9]+", label.lower()) if len(t) >= 3 and t not in _STOP}


@dataclass
class ReturnSeed:
    # Resolved scope filters against the COVERER's mailbox:
    date_from: date | None
    date_to: date | None
    included_project_ids: list[str] = field(default_factory=list)   # coverer-side
    included_person_ids: list[str] = field(default_factory=list)    # coverer-side
    allowed_domains: list[str] = field(default_factory=list)        # display/provenance
    # Provenance for handoff_return_context (ORIGINAL-mailbox ids + descriptors):
    carried_project_ids: list[str] = field(default_factory=list)
    carried_person_ids: list[str] = field(default_factory=list)
    carried_domains: list[str] = field(default_factory=list)
    carried_area_labels: list[str] = field(default_factory=list)
    seed_method: str = "snapshot_hints"


def seed_return_scope(
    session,
    *,
    original_pkg: orm.HandoffPackage,
    coverer_mailbox_id: str,
    date_from: date | None,
    date_to: date | None,
) -> ReturnSeed:
    orig_mid = str(original_pkg.mailbox_id)
    orig_scope = session.get(orm.HandoffScope, original_pkg.id)

    # ── Carried descriptors from the original package (provenance) ──────────────
    carried_project_ids: set[str] = set(orig_scope.included_project_ids or []) if orig_scope else set()
    for pid in session.execute(
        select(orm.HandoffClaim.project_id).where(
            orm.HandoffClaim.package_id == original_pkg.id,
            orm.HandoffClaim.project_id.is_not(None),
        )
    ).scalars():
        carried_project_ids.add(str(pid))

    carried_person_ids: set[str] = set(orig_scope.included_person_ids or []) if orig_scope else set()

    carried_domains: set[str] = {d.lower() for d in (orig_scope.allowed_domains or [])} if orig_scope else set()
    for dom in session.execute(
        select(orm.HandoffEvidence.sender_domain).where(orm.HandoffEvidence.package_id == original_pkg.id)
    ).scalars():
        if dom:
            carried_domains.add(dom.lower())

    carried_area_labels: set[str] = set()
    if carried_project_ids:
        for lbl in session.execute(
            select(orm.Project.label).where(
                orm.Project.mailbox_id == orig_mid, orm.Project.id.in_(carried_project_ids)
            )
        ).scalars():
            if lbl:
                carried_area_labels.add(lbl)

    # ── Tier 1: structured — match labels to the coverer's own projects ────────
    label_tokens: set[str] = set()
    for lbl in carried_area_labels:
        label_tokens |= _tokens(lbl)
    coverer_project_ids: list[str] = []
    if label_tokens:
        for pid, lbl in session.execute(
            select(orm.Project.id, orm.Project.label).where(orm.Project.mailbox_id == coverer_mailbox_id)
        ).all():
            if _tokens(lbl) & label_tokens:
                coverer_project_ids.append(str(pid))

    # ── Tier 2: snapshot hints — carried people (by email) + domains → coverer ──
    orig_person_emails: set[str] = set()
    if carried_person_ids:
        for em in session.execute(
            select(orm.Identity.email).where(
                orm.Identity.mailbox_id == orig_mid,
                orm.Identity.person_id.in_(carried_person_ids),
            )
        ).scalars():
            if em:
                orig_person_emails.add(em.lower())

    coverer_person_ids: set[str] = set()
    if orig_person_emails or carried_domains:
        for pid, email in session.execute(
            select(orm.Identity.person_id, orm.Identity.email).where(
                orm.Identity.mailbox_id == coverer_mailbox_id
            )
        ).all():
            if pid is None or not email:
                continue
            e = email.lower()
            dom = e.rsplit("@", 1)[-1] if "@" in e else ""
            if e in orig_person_emails or (dom and dom in carried_domains):
                coverer_person_ids.add(str(pid))

    # ── Choose ONE resolved filter (project ids preferred) + seed_method ───────
    if coverer_project_ids:
        resolved_projects = sorted(coverer_project_ids)
        resolved_persons: list[str] = []
        seed_method = "mixed" if coverer_person_ids else "structured"
    elif coverer_person_ids:
        resolved_projects = []
        resolved_persons = sorted(coverer_person_ids)
        # structured input (had original project ids) but resolved only via hints → mixed
        seed_method = "mixed" if carried_project_ids else "snapshot_hints"
    else:
        resolved_projects = []
        resolved_persons = []
        seed_method = "snapshot_hints"  # nothing resolved → date-window-only fallback

    return ReturnSeed(
        date_from=date_from,
        date_to=date_to,
        included_project_ids=resolved_projects,
        included_person_ids=resolved_persons,
        allowed_domains=sorted(carried_domains),
        carried_project_ids=sorted(carried_project_ids),
        carried_person_ids=sorted(carried_person_ids),
        carried_domains=sorted(carried_domains),
        carried_area_labels=sorted(carried_area_labels),
        seed_method=seed_method,
    )
