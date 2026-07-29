"""Worker claim/lease mechanics (S24 / S21 §11).

Postgres-backed queue (the S21-recommended default): a worker atomically claims
one due job with `SELECT … FOR UPDATE SKIP LOCKED`, holds a lease
(`lease_expires_at`) renewed by heartbeat, and a crashed worker's expired lease
lets the job be re-claimed. No external broker.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.db import models as orm

from . import registry, service

# Atomically claim one due job: a queued job whose retry time has passed, OR a
# running job whose lease expired (crashed/stuck worker). SKIP LOCKED lets
# concurrent workers each grab a different job without blocking.
_CLAIM_SQL = text(
    """
    UPDATE job SET
        status = 'running',
        worker_id = :worker_id,
        started_at = COALESCE(started_at, now()),
        lease_expires_at = now() + make_interval(secs => :lease_seconds),
        attempt = attempt + 1,
        next_retry_at = NULL
    WHERE id = (
        SELECT id FROM job
        WHERE (status = 'queued' AND (next_retry_at IS NULL OR next_retry_at <= now()))
           OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < now())
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id;
    """
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def claim_next(db: Session, *, worker_id: str, lease_seconds: int = 60) -> orm.Job | None:
    row = db.execute(
        _CLAIM_SQL, {"worker_id": worker_id, "lease_seconds": lease_seconds}
    ).first()
    db.commit()
    if row is None:
        return None
    job = db.get(orm.Job, row[0])
    if job is not None:
        # The claim was a raw UPDATE (bypasses the ORM); refresh so the returned
        # object reflects the new status/worker_id/lease/attempt, not a stale
        # identity-map copy (SessionLocal uses expire_on_commit=False).
        db.refresh(job)
    return job


def heartbeat(db: Session, job_id: str, *, lease_seconds: int = 60) -> None:
    job = db.get(orm.Job, job_id)
    if job is not None and job.status == "running":
        job.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
        db.commit()


def _on_error(db: Session, job: orm.Job, *, category: str, message: str) -> None:
    # attempt was incremented at claim time; retry only if attempts remain.
    if job.attempt < job.max_attempts:
        service.requeue_for_retry(db, job, category=category)
    else:
        service.mark_failed(db, job, category=category, message=message)


def run_claimed(db: Session, job: orm.Job) -> None:
    handler = registry.get_handler(job.job_type)
    if handler is None:
        service.mark_failed(db, job, category="no_handler")
        return
    ctx = registry.JobContext(
        job_id=job.id, params=dict(job.params or {}), _db=db,
        _cancel_check=lambda jid: service.is_cancel_requested(db, jid),
        _progress=lambda jid, fields: service.update_progress(db, jid, fields),
    )
    try:
        result = handler(ctx)
    except registry.JobCanceled:
        service.mark_canceled(db, job)
        return
    except registry.JobError as je:
        _on_error(db, job, category=je.category, message=je.safe_message)
        return
    except Exception as e:  # never record str(e) — only the safe type name
        _on_error(db, job, category="handler_error", message=type(e).__name__)
        return
    if result is not None and result.status == "partially_succeeded":
        service.mark_partial(db, job, summary=result.summary, progress=result.progress)
    else:
        service.mark_succeeded(
            db, job,
            summary=(result.summary if result else None),
            progress=(result.progress if result else None),
        )


def run_once(db: Session, *, worker_id: str, lease_seconds: int = 60) -> orm.Job | None:
    """Claim + run one job. Returns the (refreshed) job, or None if none due."""
    job = claim_next(db, worker_id=worker_id, lease_seconds=lease_seconds)
    if job is None:
        return None
    run_claimed(db, job)
    db.refresh(job)
    return job
