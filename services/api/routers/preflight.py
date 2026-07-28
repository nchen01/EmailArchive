"""GET /api/preflight — operational readiness check (S8.3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from services.preflight import PreflightCheck, run_checks

from ..auth import get_principal

router = APIRouter(tags=["preflight"])


class CheckOut(BaseModel):
    name: str
    status: str
    message: str


class PreflightOut(BaseModel):
    ok: bool
    checks: list[CheckOut]


def _to_out(c: PreflightCheck) -> CheckOut:
    return CheckOut(name=c.name, status=c.status, message=c.message)


# Preflight is a readiness/diagnostic probe, not a mailbox-content route, so it is
# guarded by authentication only (fail-closed in production via get_principal) and
# NOT by mailbox ownership — it returns no mailbox content, and callers pass
# arbitrary/unknown mailbox ids to check embedding readiness. (S22)
@router.get(
    "/preflight", response_model=PreflightOut,
    dependencies=[Depends(get_principal)],
)
async def get_preflight(
    mailbox_id: str | None = Query(None, description="UUID of mailbox to verify embeddings for"),
) -> PreflightOut:
    checks = run_checks(mailbox_id=mailbox_id)
    ok = not any(c.failed for c in checks)
    return PreflightOut(ok=ok, checks=[_to_out(c) for c in checks])
