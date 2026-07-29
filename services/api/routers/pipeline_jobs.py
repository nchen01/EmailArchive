"""Post-ingest pipeline enqueue endpoints (S26).

Creator/owner-guarded (S22) endpoints that enqueue the S24 pipeline jobs in
dependency order. Cost/readiness gates live here (request-time), before enqueue:

  POST /api/mailbox/{id}/pipeline/enrich            → l1_enrichment
  POST /api/mailbox/{id}/pipeline/extract-events    → event_extraction
  POST /api/mailbox/{id}/pipeline/embed-backfill    → embedding_backfill
       (COST GATE: no `confirm` returns a dry-run estimate + does NOT enqueue;
        `confirm: true` enqueues with cost_confirmed=true — no live Voyage call
        without this explicit operator confirm.)
  POST /api/mailbox/{id}/pipeline/materialize       → project_materialization
       (requires embeddings first — S9)

Responses carry safe metadata only. Recipients have no principal and cannot reach
these routes; recipient package access is unaffected.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.db import models as orm
from services.jobs import service

from ..auth import Principal, get_principal, require_owner_mailbox
from ..deps import get_db

router = APIRouter(tags=["pipeline"])


class EnqueuedJob(BaseModel):
    job_id: str
    status: str
    job_type: str


class EmbedBackfillRequest(BaseModel):
    confirm: bool = False
    batch_size: int = Field(default=64, ge=1, le=2048)
    batch_delay_seconds: float = Field(default=0.0, ge=0.0, le=600.0)


class EmbedEstimate(BaseModel):
    needs_confirm: bool = True
    to_embed: int
    already_embedded: int
    total_messages: int
    est_tokens: int
    est_cost_usd: float
    model: str


class MaterializeRequest(BaseModel):
    limit_threads: int | None = Field(default=None, ge=1)


def _enqueue(db, principal, *, mailbox_id, job_type, params, idem) -> EnqueuedJob:
    job = service.enqueue(
        db, tenant_id=principal.tenant_id, job_type=job_type, mailbox_id=mailbox_id,
        requested_by=principal.user_id, params=params, idempotency_key=idem,
    )
    return EnqueuedJob(job_id=str(job.id), status=job.status, job_type=job.job_type)


@router.post("/mailbox/{mailbox_id}/pipeline/enrich", response_model=EnqueuedJob)
async def enqueue_enrich(
    mailbox_id: str,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> EnqueuedJob:
    return _enqueue(db, principal, mailbox_id=str(mbx.id), job_type="l1_enrichment",
                    params={"mailbox_id": str(mbx.id)}, idem=f"l1_enrichment:{mbx.id}")


@router.post("/mailbox/{mailbox_id}/pipeline/extract-events", response_model=EnqueuedJob)
async def enqueue_extract_events(
    mailbox_id: str,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> EnqueuedJob:
    return _enqueue(db, principal, mailbox_id=str(mbx.id), job_type="event_extraction",
                    params={"mailbox_id": str(mbx.id)}, idem=f"event_extraction:{mbx.id}")


@router.post("/mailbox/{mailbox_id}/pipeline/embed-backfill",
             response_model=EnqueuedJob | EmbedEstimate)
async def enqueue_embed_backfill(
    mailbox_id: str,
    body: EmbedBackfillRequest,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
):
    """Cost gate: without `confirm`, return a dry-run estimate (no enqueue, no
    Voyage call). With `confirm: true`, enqueue the backfill."""
    if not body.confirm:
        from scripts.embed_backfill import _DryRunEmbedClient, run_backfill
        stats = run_backfill(
            mailbox_id=str(mbx.id), embed_client=_DryRunEmbedClient("voyage-4"),
            dry_run=True, session=db,
        )
        return EmbedEstimate(
            to_embed=stats["to_embed"], already_embedded=stats["already_embedded"],
            total_messages=stats["total_messages"], est_tokens=stats["est_tokens"],
            est_cost_usd=stats["est_cost_usd"], model=stats["model"],
        )
    return _enqueue(
        db, principal, mailbox_id=str(mbx.id), job_type="embedding_backfill",
        params={"mailbox_id": str(mbx.id), "cost_confirmed": True,
                "batch_size": body.batch_size, "batch_delay_seconds": body.batch_delay_seconds},
        idem=f"embedding_backfill:{mbx.id}:{body.batch_size}",
    )


@router.post("/mailbox/{mailbox_id}/pipeline/materialize", response_model=EnqueuedJob)
async def enqueue_materialize(
    mailbox_id: str,
    body: MaterializeRequest,
    principal: Principal = Depends(get_principal),
    mbx: orm.Mailbox = Depends(require_owner_mailbox),
    db: Session = Depends(get_db),
) -> EnqueuedJob:
    """S9 gate: materialization requires embeddings first (no silent zero-vector
    clustering) — reject fast if the mailbox has no voyage-4 embeddings."""
    n_embed = db.execute(
        select(func.count()).select_from(orm.MessageEmbedding).where(
            orm.MessageEmbedding.mailbox_id == str(mbx.id)
        )
    ).scalar_one()
    if not n_embed:
        raise HTTPException(
            status_code=409,
            detail="no embeddings for this mailbox — run embedding backfill first",
        )
    return _enqueue(
        db, principal, mailbox_id=str(mbx.id), job_type="project_materialization",
        params={"mailbox_id": str(mbx.id),
                **({"limit_threads": body.limit_threads} if body.limit_threads else {})},
        idem=f"project_materialization:{mbx.id}:{body.limit_threads or 'all'}",
    )
