"""Background job endpoints (S24 — implements docs/s21-background-job-orchestration-plan.md).

  POST /api/mailbox/{mailbox_id}/jobs        enqueue a job for an owned mailbox
  GET  /api/mailbox/{mailbox_id}/jobs        list jobs for an owned mailbox
  GET  /api/jobs/{job_id}                     job status (owner of the job's mailbox)
  POST /api/jobs/{job_id}/cancel             request cancel

All guarded by the S22 owner/tenant boundary. Cross-tenant / not-owned jobs return
404 (no existence oracle). Responses carry SAFE metadata only. Recipients have no
principal and cannot reach these routes; recipient package access is unaffected.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.db import models as orm
from services.jobs import registry, service

from ..auth import Principal, get_principal, owned_mailbox_or_none, require_owner_mailbox
from ..deps import get_db

router = APIRouter(tags=["jobs"])


class EnqueueJobRequest(BaseModel):
    job_type: str
    params: dict = Field(default_factory=dict)
    idempotency_key: str | None = None


class JobOut(BaseModel):
    id: str
    tenant_id: str
    mailbox_id: str | None
    job_type: str
    status: str
    params: dict
    progress: dict
    summary: str | None
    error_category: str | None
    error_message: str | None
    attempt: int
    max_attempts: int
    created_at: str | None
    started_at: str | None
    finished_at: str | None


def _out(job: orm.Job) -> JobOut:
    return JobOut(
        id=str(job.id), tenant_id=str(job.tenant_id),
        mailbox_id=str(job.mailbox_id) if job.mailbox_id else None,
        job_type=job.job_type, status=job.status, params=dict(job.params or {}),
        progress=dict(job.progress or {}), summary=job.summary,
        error_category=job.error_category, error_message=job.error_message,
        attempt=job.attempt, max_attempts=job.max_attempts,
        created_at=job.created_at.isoformat() if job.created_at else None,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


def _job_owned_or_404(db: Session, principal: Principal, job_id: str) -> orm.Job:
    job = service.get(db, job_id)
    if job is None or str(job.tenant_id) != principal.tenant_id:
        raise HTTPException(status_code=404, detail="Not found.")
    # Mailbox-scoped jobs also require ownership of the mailbox.
    if job.mailbox_id and owned_mailbox_or_none(db, principal, str(job.mailbox_id)) is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return job


@router.post("/mailbox/{mailbox_id}/jobs", response_model=JobOut)
async def enqueue_job(
    mailbox_id: str,
    body: EnqueueJobRequest,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> JobOut:
    if body.job_type not in registry.API_ENQUEUABLE:
        raise HTTPException(status_code=422, detail=f"job_type not enqueuable: {body.job_type}")
    job = service.enqueue(
        db, tenant_id=principal.tenant_id, job_type=body.job_type, params=body.params,
        mailbox_id=str(mbx.id), requested_by=principal.user_id,
        idempotency_key=body.idempotency_key,
    )
    return _out(job)


@router.get("/mailbox/{mailbox_id}/jobs", response_model=list[JobOut])
async def list_jobs(
    mailbox_id: str,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    jobs = service.list_for_mailbox(db, tenant_id=principal.tenant_id, mailbox_id=str(mbx.id))
    return [_out(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JobOut:
    return _out(_job_owned_or_404(db, principal, job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JobOut:
    job = _job_owned_or_404(db, principal, job_id)
    return _out(service.request_cancel(db, job))
