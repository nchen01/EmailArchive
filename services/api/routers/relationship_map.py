"""Relationship-map read endpoint (S13.3).

A separate router from network-map — it derives a richer, graph-backed relationship
model live (services/relationships/derive.py) instead of reading the owner-centric
Edge graph. The existing /api/network-map endpoints are untouched.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from services.db import models as orm
from services.relationships.contracts import RelationshipMapResponse
from services.relationships.derive import derive_relationship_map

from ..deps import get_db

router = APIRouter(tags=["relationship-map"])

_VALID_MODES = {"owner", "project", "org", "graph"}
_VALID_TYPES = {
    "direct_exchange",
    "thread_copresence",
    "project_copresence",
    "org_affiliation",
    "bridge",
}


@router.get("/relationship-map/{mailbox_id}", response_model=RelationshipMapResponse)
async def get_relationship_map(
    mailbox_id: str,
    mode: str = Query("owner"),
    root_id: str | None = Query(None),
    project_id: str | None = Query(None),
    min_weight: float = Query(0.0, ge=0.0),
    recency_days: int | None = Query(None, ge=0),
    relationship_types: list[str] | None = Query(None),
    db: Session = Depends(get_db),
) -> RelationshipMapResponse:
    mbx = db.get(orm.Mailbox, mailbox_id)
    if mbx is None:
        raise HTTPException(status_code=404, detail="mailbox not found")

    if mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail=f"invalid mode: {mode}")

    rel_types: list[str] | None = None
    if relationship_types:
        rel_types = [t for t in relationship_types if t in _VALID_TYPES]
        # If the caller passed only invalid types, treat as no filter rather than
        # silently returning an empty map.
        if not rel_types:
            rel_types = None

    return derive_relationship_map(
        db,
        mailbox_id,
        mbx,
        mode=mode,
        root_id=root_id,
        project_id=project_id,
        min_weight=min_weight,
        relationship_types=rel_types,
        recency_days=recency_days,
    )
