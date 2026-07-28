"""Project-view read endpoints (spec 02 §5).

Reads the clustering output materialized by L1 (project / project_member /
thread_project_assignment) and assembles the §5 list + detail shapes.

Scope (S3): NO Events / L3 summaries — the ``activity`` field is S4 and returned
empty (spec 02 §5 marks it ``@sprint S4``). Derived state uses thread recency
only (spec 02 §4, Event-free variant per Step 8).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.db import models as orm

from ..auth import require_owner_mailbox
from ..deps import get_db
from ..schemas.project_view import (
    ActivityItemOut,
    ProjectDetailOut,
    ProjectListOut,
    ProjectMemberOut,
    ProjectMetrics,
    ProjectSummaryOut,
    RecentThreadOut,
    WhoToAsk,
)

router = APIRouter(tags=["project-view"])

# Epistemic grade ordering for the activity panel: outcome > did > proposed
# (spec 02 §6). Lower rank renders first.
_GRADE_RANK = {"outcome": 0, "did": 1, "proposed": 2}

# Fixed reference "now" for deterministic state derivation (mirrors the L1 graph
# module default). A real deployment would inject wall-clock per request.
_DEFAULT_NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)

STALE_DAYS = 30
WHO_TO_ASK_LIMIT = 4
RECENT_THREADS_LIMIT = 10


def _get_mailbox(db: Session, mailbox_id: str) -> orm.Mailbox:
    mbx = db.get(orm.Mailbox, mailbox_id)
    if mbx is None:
        raise HTTPException(status_code=404, detail="mailbox not found")
    return mbx


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def project_state(
    thread_ends: list[datetime],
    *,
    has_recent_outcome: bool = False,
    now: datetime = _DEFAULT_NOW,
) -> str:
    """Derived state (spec 02 §4).

    stale:    last thread activity > STALE_DAYS ago.
    shipping: stale threshold not met AND at least one outcome Event exists.
    active:   otherwise.
    """
    if not thread_ends:
        return "stale"
    last = max(_aware(t) for t in thread_ends)
    if (now - last).days > STALE_DAYS:
        return "stale"
    if has_recent_outcome:
        return "shipping"
    return "active"


def _person_name(person: orm.Person) -> str:
    return person.names[0] if person.names else person.canonical_email


def _activity_for_project(
    db: Session, mailbox_id: str, project_id: str, *, now: datetime = _DEFAULT_NOW
) -> tuple[list[ActivityItemOut], bool]:
    """Build the ordered activity panel and check for recent outcomes (spec 02 §6).

    Returns ``(items, has_recent_outcome)`` where ``has_recent_outcome`` is True
    only when at least one outcome Event has a max-cited-message timestamp within
    STALE_DAYS of ``now``.

    Ordered by epistemic grade (outcome > did > proposed) then MAX cited ts.
    """
    events = list(
        db.execute(
            select(orm.Event).where(
                orm.Event.mailbox_id == mailbox_id,
                orm.Event.project_id == project_id,
            )
        ).scalars()
    )
    if not events:
        return [], False

    # MAX cited ts per event: map each citation header -> message.ts, take max.
    all_headers = sorted({h for e in events for h in e.source_message_ids})
    header_ts: dict[str, datetime] = {}
    if all_headers:
        for header, ts in db.execute(
            select(orm.Message.message_id_header, orm.Message.ts).where(
                orm.Message.mailbox_id == mailbox_id,
                orm.Message.message_id_header.in_(all_headers),
            )
        ):
            header_ts[header] = ts

    _EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def _max_ts(e: orm.Event) -> datetime:
        cited = [header_ts[h] for h in e.source_message_ids if h in header_ts]
        return max((_aware(t) for t in cited), default=_EPOCH)

    # Compute once; reused for ordering and freshness check.
    event_max_ts = {id(e): _max_ts(e) for e in events}

    ordered = sorted(
        events,
        key=lambda e: (_GRADE_RANK.get(e.type, 99), -event_max_ts[id(e)].timestamp()),
    )

    cutoff = _aware(now) - timedelta(days=STALE_DAYS)
    has_recent_outcome = any(
        e.type == "outcome" and event_max_ts[id(e)] >= cutoff
        for e in events
    )

    items: list[ActivityItemOut] = []
    for e in ordered:
        if not e.source_message_ids:
            continue
        items.append(
            ActivityItemOut(
                type=e.type,
                summary=e.summary,
                actor_person_id=str(e.actor_person_id),
                source_message_ids=list(e.source_message_ids),
                confidence=float(e.confidence),
            )
        )
    return items, has_recent_outcome


def _recent_outcome_project_ids(
    db: Session, mailbox_id: str, now: datetime = _DEFAULT_NOW
) -> set[str]:
    """Project IDs with an outcome Event whose max-cited message.ts is within STALE_DAYS.

    Two queries for the whole mailbox — no N+1.
    """
    outcome_rows = list(
        db.execute(
            select(orm.Event.project_id, orm.Event.source_message_ids).where(
                orm.Event.mailbox_id == mailbox_id,
                orm.Event.type == "outcome",
                orm.Event.project_id.is_not(None),
            )
        ).all()
    )
    if not outcome_rows:
        return set()

    all_cited = sorted({h for _, sids in outcome_rows for h in sids})
    header_ts_map: dict[str, datetime] = dict(
        db.execute(
            select(orm.Message.message_id_header, orm.Message.ts).where(
                orm.Message.mailbox_id == mailbox_id,
                orm.Message.message_id_header.in_(all_cited),
            )
        ).all()
    )

    cutoff = _aware(now) - timedelta(days=STALE_DAYS)
    recent: set[str] = set()
    for pid, sids in outcome_rows:
        cited_ts = [_aware(header_ts_map[h]) for h in sids if h in header_ts_map]
        if cited_ts and max(cited_ts) >= cutoff:
            recent.add(str(pid))
    return recent


@router.get(
    "/projects/{mailbox_id}", response_model=ProjectListOut,
    dependencies=[Depends(require_owner_mailbox)],
)
async def list_projects(
    mailbox_id: str, db: Session = Depends(get_db)
) -> ProjectListOut:
    _get_mailbox(db, mailbox_id)

    projects = list(
        db.execute(
            select(orm.Project)
            .where(orm.Project.mailbox_id == mailbox_id)
            .order_by(orm.Project.confidence.desc(), orm.Project.label)
        ).scalars()
    )

    # Freshness-gated outcome project IDs (two queries for the whole mailbox).
    outcome_project_ids = _recent_outcome_project_ids(db, mailbox_id)

    summaries: list[ProjectSummaryOut] = []
    for p in projects:
        assigns = list(
            db.execute(
                select(orm.ThreadProjectAssignment.thread_id).where(
                    orm.ThreadProjectAssignment.project_id == p.id
                )
            ).scalars()
        )
        member_count = db.execute(
            select(orm.ProjectMember).where(orm.ProjectMember.project_id == p.id)
        ).scalars().all()
        thread_ends = list(
            db.execute(
                select(orm.Thread.t_end).where(orm.Thread.id.in_(assigns))
            ).scalars()
        ) if assigns else []
        summaries.append(
            ProjectSummaryOut(
                id=str(p.id),
                label=p.label,
                state=project_state(
                    thread_ends,
                    has_recent_outcome=str(p.id) in outcome_project_ids,
                ),
                confidence=float(p.confidence),
                start=p.start,
                end=p.end,
                member_count=len(member_count),
                thread_count=len(set(assigns)),
            )
        )

    return ProjectListOut(projects=summaries)


@router.get(
    "/projects/{mailbox_id}/{project_id}", response_model=ProjectDetailOut,
    dependencies=[Depends(require_owner_mailbox)],
)
async def get_project_detail(
    mailbox_id: str, project_id: str, db: Session = Depends(get_db)
) -> ProjectDetailOut:
    _get_mailbox(db, mailbox_id)

    project = db.get(orm.Project, project_id)
    if project is None or str(project.mailbox_id) != str(mailbox_id):
        raise HTTPException(status_code=404, detail="project not found")

    # Members (with involvement / in-project message_count).
    members = list(
        db.execute(
            select(orm.ProjectMember)
            .where(orm.ProjectMember.project_id == project_id)
            .order_by(orm.ProjectMember.involvement.desc())
        ).scalars()
    )
    person_ids = [m.person_id for m in members]
    persons: dict = {}
    if person_ids:
        for pr in db.execute(
            select(orm.Person).where(orm.Person.id.in_(person_ids))
        ).scalars():
            persons[str(pr.id)] = pr

    # Edge weights for who-to-ask ranking (global owner<->contact weight).
    edge_weight: dict = {}
    if person_ids:
        for e in db.execute(
            select(orm.Edge).where(
                orm.Edge.mailbox_id == mailbox_id, orm.Edge.person_id.in_(person_ids)
            )
        ).scalars():
            edge_weight[str(e.person_id)] = float(e.weight)

    member_outs: list[ProjectMemberOut] = []
    for m in members:
        pr = persons.get(str(m.person_id))
        member_outs.append(
            ProjectMemberOut(
                person_id=str(m.person_id),
                name=_person_name(pr) if pr else str(m.person_id),
                role=pr.role if pr else "unknown",
                in_project_count=m.message_count,
                involvement=float(m.involvement),
            )
        )

    # Who to ask: top members by in-project edge weight, ties by involvement desc.
    ranked = sorted(
        members,
        key=lambda m: (-edge_weight.get(str(m.person_id), 0.0), -float(m.involvement), str(m.person_id)),
    )
    who_to_ask: list[WhoToAsk] = []
    for m in ranked[:WHO_TO_ASK_LIMIT]:
        pr = persons.get(str(m.person_id))
        who_to_ask.append(
            WhoToAsk(
                person_id=str(m.person_id),
                name=_person_name(pr) if pr else str(m.person_id),
                role=pr.role if pr else "unknown",
                in_project_count=m.message_count,
                weight=round(edge_weight.get(str(m.person_id), 0.0), 4),
            )
        )

    # Threads.
    thread_ids = list(
        db.execute(
            select(orm.ThreadProjectAssignment.thread_id).where(
                orm.ThreadProjectAssignment.project_id == project_id
            )
        ).scalars()
    )
    thread_ids = sorted(set(thread_ids))
    threads = []
    if thread_ids:
        threads = list(
            db.execute(
                select(orm.Thread)
                .where(orm.Thread.id.in_(thread_ids))
                .order_by(orm.Thread.t_end.desc())
            ).scalars()
        )

    recent = [
        RecentThreadOut(
            thread_id=str(t.id),
            subject=t.subject_norm,
            participants=list(t.participants),
            last=t.t_end,
        )
        for t in threads[:RECENT_THREADS_LIMIT]
    ]

    last_activity = max((t.t_end for t in threads), default=project.end)
    metrics = ProjectMetrics(
        members=len(members),
        threads=len(thread_ids),
        last_activity=last_activity,
    )

    activity, has_recent_outcome = _activity_for_project(db, mailbox_id, project_id)

    return ProjectDetailOut(
        id=str(project.id),
        label=project.label,
        state=project_state(
            [t.t_end for t in threads],
            has_recent_outcome=has_recent_outcome,
        ),
        confidence=float(project.confidence),
        start=project.start,
        end=project.end,
        metrics=metrics,
        who_to_ask=who_to_ask,
        members=member_outs,
        recent_threads=recent,
        activity=activity,
    )
