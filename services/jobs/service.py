"""Job service — enqueue / status / cancel / progress / state transitions (S24).

Tenant-scoped. All caller-supplied metadata is sanitized (sanitize.py) before it
touches a job row, so params/progress/summary/errors never carry content/secrets.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.db import models as orm

from . import registry
from .sanitize import safe_summary, sanitize_metadata

ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "failed", "canceled", "partially_succeeded")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, job: orm.Job, action: str) -> None:
    """Best-effort safe audit of a job lifecycle event (mailbox-scoped jobs only).
    Safe metadata only — action + job_type category, never params/progress content."""
    if not job.mailbox_id:
        return
    try:
        db.add(orm.AuditLog(
            mailbox_id=job.mailbox_id,
            actor=(job.requested_by_user_id or "system"),
            action=action, scope=job.job_type, started_at=_now(),
        ))
        db.commit()
    except Exception:
        db.rollback()


def enqueue(
    db: Session, *, tenant_id: str, job_type: str, params: dict | None = None,
    mailbox_id: str | None = None, requested_by: str | None = None,
    idempotency_key: str | None = None, max_attempts: int = 1,
) -> orm.Job:
    if not registry.is_registered(job_type):
        raise ValueError(f"unknown job_type: {job_type}")

    # Dedupe: an active job with the same (tenant, key) is returned as-is.
    if idempotency_key:
        existing = db.execute(
            select(orm.Job).where(
                orm.Job.tenant_id == tenant_id,
                orm.Job.idempotency_key == idempotency_key,
                orm.Job.status.in_(ACTIVE),
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    job = orm.Job(
        tenant_id=tenant_id, requested_by_user_id=requested_by, mailbox_id=mailbox_id,
        job_type=job_type, status="queued", params=sanitize_metadata(params),
        idempotency_key=idempotency_key, progress={}, max_attempts=max(1, int(max_attempts)),
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # Lost an enqueue race on the partial-unique index — return the winner.
        db.rollback()
        if idempotency_key:
            existing = db.execute(
                select(orm.Job).where(
                    orm.Job.tenant_id == tenant_id,
                    orm.Job.idempotency_key == idempotency_key,
                    orm.Job.status.in_(ACTIVE),
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
        raise
    _audit(db, job, "job_created")
    return job


def get(db: Session, job_id: str) -> orm.Job | None:
    return db.get(orm.Job, job_id)


def list_for_mailbox(db: Session, *, tenant_id: str, mailbox_id: str, limit: int = 50) -> list[orm.Job]:
    return list(db.execute(
        select(orm.Job).where(
            orm.Job.tenant_id == tenant_id, orm.Job.mailbox_id == mailbox_id
        ).order_by(orm.Job.created_at.desc()).limit(limit)
    ).scalars())


def is_cancel_requested(db: Session, job_id: str) -> bool:
    job = db.get(orm.Job, job_id)
    return bool(job and job.cancel_requested_at is not None)


def request_cancel(db: Session, job: orm.Job) -> orm.Job:
    if job.status in TERMINAL:
        return job  # already done — idempotent no-op
    job.cancel_requested_at = job.cancel_requested_at or _now()
    if job.status == "queued":
        # Not started — cancel immediately.
        job.status = "canceled"
        job.finished_at = _now()
    db.commit()
    _audit(db, job, "job_canceled")
    return job


def update_progress(db: Session, job_id: str, fields: dict) -> None:
    job = db.get(orm.Job, job_id)
    if job is None:
        return
    merged = dict(job.progress or {})
    merged.update(sanitize_metadata(fields))
    job.progress = merged
    db.commit()


# ── Terminal / running transitions (used by the worker) ──────────────────────

def mark_succeeded(db: Session, job: orm.Job, *, summary=None, progress=None) -> None:
    job.status = "succeeded"
    job.summary = safe_summary(summary)
    if progress:
        job.progress = {**(job.progress or {}), **sanitize_metadata(progress)}
    job.finished_at = _now()
    db.commit()
    _audit(db, job, "job_succeeded")


def mark_partial(db: Session, job: orm.Job, *, summary=None, progress=None) -> None:
    job.status = "partially_succeeded"
    job.summary = safe_summary(summary)
    if progress:
        job.progress = {**(job.progress or {}), **sanitize_metadata(progress)}
    job.finished_at = _now()
    db.commit()
    _audit(db, job, "job_partial_success")


def mark_canceled(db: Session, job: orm.Job) -> None:
    job.status = "canceled"
    job.finished_at = _now()
    db.commit()
    _audit(db, job, "job_canceled")


def mark_failed(db: Session, job: orm.Job, *, category: str, message: str = "") -> None:
    job.status = "failed"
    job.error_category = str(category)[:80]
    job.error_message = safe_summary(message)  # already content-free from the caller
    job.finished_at = _now()
    db.commit()
    _audit(db, job, "job_failed")


def requeue_for_retry(db: Session, job: orm.Job, *, category: str, backoff_seconds: int = 30) -> None:
    job.status = "queued"
    job.error_category = str(category)[:80]
    job.next_retry_at = _now() + timedelta(seconds=backoff_seconds)
    job.lease_expires_at = None
    job.worker_id = None
    db.commit()
    _audit(db, job, "job_retry_scheduled")
