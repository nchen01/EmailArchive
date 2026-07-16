"""Deterministic candidate handoff-package generator (S17.3).

Builds a *candidate* package (claims + snapshotted evidence + exclusions) from an
existing mailbox's L1 outputs and an explicit scope. No new synthesis algorithm:
claims are derived from already-extracted, already-cited `Event` rows.

Hard invariants enforced here:
- No sensitive (whole-thread) or creator-excluded message can enter
  `handoff_evidence` — the sensitivity/exclusion gate runs before snapshotting.
- Every persisted `handoff_claim` cites >=1 in-package `handoff_evidence` row;
  claims with no surviving in-scope citation are dropped.

Idempotent: regenerating replaces the package's claims/evidence/exclusions.
Publish/recipient behavior is out of scope for S17.3.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete, or_, select, text as sqltext

from services.api.evidence import sender_fields
from services.db import models as orm
from services.handoff.audit import write_handoff_audit
from services.ingest.normalize.mime import decode_mime_words

_BODY_SNAPSHOT_CHARS = 2000
# Event type -> claim kind. proposed work is an open loop; done/outcome are decisions.
_EVENT_KIND = {"proposed": "open_loop", "did": "decision", "outcome": "decision"}


def _eligible_thread_ids(session, mailbox_id: str) -> set[str]:
    """Threads with NO non-{none} message (whole-thread sensitivity gate, S9/S13/S14)."""
    rows = session.execute(
        sqltext(
            "SELECT t.id::text FROM thread t WHERE t.mailbox_id = :mid "
            "AND NOT EXISTS (SELECT 1 FROM message m2 WHERE m2.thread_id = t.id "
            "  AND m2.mailbox_id = :mid AND m2.sensitivity != '{none}')"
        ),
        {"mid": mailbox_id},
    ).all()
    return {r[0] for r in rows}


def _in_scope(session, mailbox_id: str, scope: orm.HandoffScope, eligible: set[str]):
    """Return (safe_headers, sensitivity_excluded_thread_ids) for the scope.

    Scope filters (AND when set): date window; included_thread_ids (most specific)
    else included_project_ids (via thread_project_assignment); included_person_ids
    (sender/recipient identity match). Creator exclusions and the whole-thread
    sensitivity gate remove messages before they can become evidence.
    """
    # Noise (newsletters/automation residue) is never package evidence.
    q = select(orm.Message.message_id_header, orm.Message.thread_id).where(
        orm.Message.mailbox_id == mailbox_id,
        orm.Message.noise == False,  # noqa: E712
    )

    if scope.date_from is not None:
        q = q.where(orm.Message.ts >= datetime.combine(scope.date_from, time.min, tzinfo=timezone.utc))
    if scope.date_to is not None:
        upper = datetime.combine(scope.date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        q = q.where(orm.Message.ts < upper)  # inclusive date_to

    if scope.included_thread_ids:
        q = q.where(orm.Message.thread_id.in_(scope.included_thread_ids))
    elif scope.included_project_ids:
        thread_sub = (
            select(orm.ThreadProjectAssignment.thread_id)
            .where(orm.ThreadProjectAssignment.project_id.in_(scope.included_project_ids))
        )
        q = q.where(orm.Message.thread_id.in_(thread_sub))

    if scope.included_person_ids:
        emails = list(session.execute(
            select(orm.Identity.email).where(
                orm.Identity.mailbox_id == mailbox_id,
                orm.Identity.person_id.in_(scope.included_person_ids),
            )
        ).scalars())
        if emails:
            q = q.where(or_(
                orm.Message.sender_email.in_(emails),
                orm.Message.to_emails.overlap(emails),
                orm.Message.cc_emails.overlap(emails),
            ))
        else:
            return set(), set()  # requested people have no known addresses → empty scope

    excluded_threads = set(scope.excluded_thread_ids or [])
    excluded_headers = set(scope.excluded_message_id_headers or [])

    safe_headers: set[str] = set()
    sens_excluded_threads: set[str] = set()
    for header, thread_id in session.execute(q).all():
        tid = str(thread_id)
        if tid in excluded_threads or header in excluded_headers:
            continue  # creator-removed (recorded separately from scope)
        if tid not in eligible:
            sens_excluded_threads.add(tid)  # whole-thread sensitivity → never evidence
            continue
        safe_headers.add(header)
    return safe_headers, sens_excluded_threads


def _claims_from_events(session, mailbox_id: str, scope: orm.HandoffScope, safe_headers: set[str]):
    """Candidate claims derived from Events whose sources are in-scope + safe."""
    included_projects = set(scope.included_project_ids or [])
    claims: list[dict] = []
    for ev in session.execute(
        select(orm.Event).where(orm.Event.mailbox_id == mailbox_id)
    ).scalars():
        kind = _EVENT_KIND.get(ev.type)
        if kind is None:
            continue
        if included_projects and ev.project_id and str(ev.project_id) not in included_projects:
            continue  # event belongs to an out-of-scope project
        headers = [h for h in ev.source_message_ids if h in safe_headers]
        if not headers:
            continue  # no in-scope safe citation → cannot be a package claim
        claims.append({
            "kind": kind,
            "text": ev.summary,
            "project_id": str(ev.project_id) if ev.project_id else None,
            "headers": headers,
            "confidence": float(ev.confidence),
        })
    return claims


def _snapshot_evidence(session, mailbox_id: str, headers: list[str]):
    """Snapshot safe message content for the cited headers (no raw MIME, no sensitive)."""
    if not headers:
        return []
    rows = session.execute(
        select(
            orm.Message.message_id_header, orm.Message.subject, orm.Message.ts,
            orm.Message.clean_text, orm.Message.sender_email, orm.Message.addresses,
        ).where(
            orm.Message.mailbox_id == mailbox_id,
            orm.Message.message_id_header.in_(headers),
        )
    ).all()
    evidence = []
    for r in rows:
        display, domain = sender_fields(r.sender_email, r.addresses)
        evidence.append({
            "message_id_header": r.message_id_header,
            "subject": decode_mime_words(r.subject or ""),
            "sender_display": display,
            "sender_domain": domain,
            "ts": r.ts,
            "body_snapshot": (r.clean_text or "")[:_BODY_SNAPSHOT_CHARS],
            "source_type": "l1_structured",
        })
    return evidence


def generate_candidate(session, package: orm.HandoffPackage) -> dict:
    """Generate/regenerate the candidate package for ``package``; return counts.

    Sets status -> generated and writes a `candidate_generated` audit row.
    """
    mailbox_id = package.mailbox_id
    scope = session.get(orm.HandoffScope, package.id)
    if scope is None:  # a package always has a scope row (created with the package)
        scope = orm.HandoffScope(package_id=package.id)
        session.add(scope)
        session.flush()

    eligible = _eligible_thread_ids(session, mailbox_id)
    safe_headers, sens_excluded_threads = _in_scope(session, mailbox_id, scope, eligible)

    claims = _claims_from_events(session, mailbox_id, scope, safe_headers)
    cited = sorted({h for c in claims for h in c["headers"]})
    evidence = _snapshot_evidence(session, mailbox_id, cited)
    evid_headers = {e["message_id_header"] for e in evidence}

    # Invariant: drop claims whose citations produced no in-package evidence, and
    # trim each claim's citations to headers that are actually in the package.
    kept: list[dict] = []
    for c in claims:
        c_headers = [h for h in c["headers"] if h in evid_headers]
        if c_headers:
            c["headers"] = c_headers
            kept.append(c)
    claims = kept

    # ── Idempotent replace ────────────────────────────────────────────────────
    for model in (orm.HandoffClaim, orm.HandoffEvidence, orm.HandoffExclusion):
        session.execute(delete(model).where(model.package_id == package.id))

    for e in evidence:
        session.add(orm.HandoffEvidence(
            package_id=package.id, message_id_header=e["message_id_header"],
            subject=e["subject"], sender_display=e["sender_display"],
            sender_domain=e["sender_domain"], ts=e["ts"],
            body_snapshot=e["body_snapshot"], source_type=e["source_type"],
        ))
    for c in claims:
        session.add(orm.HandoffClaim(
            package_id=package.id, kind=c["kind"], text=c["text"],
            project_id=c["project_id"], source_message_id_headers=c["headers"],
            confidence=c["confidence"],
        ))

    # Exclusions (creator/audit-only; never shown to a recipient).
    for tid in sorted(scope.excluded_thread_ids or []):
        session.add(orm.HandoffExclusion(
            package_id=package.id, exclusion_type="user_removed", target_type="thread",
            target_ref=str(tid), aggregate_label="user_removed",
        ))
    for hdr in sorted(scope.excluded_message_id_headers or []):
        session.add(orm.HandoffExclusion(
            package_id=package.id, exclusion_type="user_removed", target_type="message",
            target_ref=hdr, aggregate_label="user_removed",
        ))
    for tid in sorted(sens_excluded_threads):
        session.add(orm.HandoffExclusion(
            package_id=package.id, exclusion_type="sensitivity", target_type="thread",
            target_ref=tid, aggregate_label="sensitivity",
        ))

    package.status = "generated"
    package.updated_at = datetime.now(timezone.utc)
    session.commit()

    counts = {
        "claims": len(claims),
        "evidence": len(evidence),
        "excluded_sensitivity": len(sens_excluded_threads),
        "excluded_user": len(scope.excluded_thread_ids or []) + len(scope.excluded_message_id_headers or []),
    }
    write_handoff_audit(
        session, package_id=package.id, lineage_id=package.lineage_id,
        actor=f"owner:{package.creator_email}", action="candidate_generated",
        metadata=counts,
    )
    return counts
