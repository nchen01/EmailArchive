"""Job type registry + handler contract (S24).

A handler is `Callable[[JobContext], JobResult | None]`:
  - return a JobResult (or None → succeeded) for success / partial success,
  - raise JobCanceled to end as canceled (checkpoint honored a cancel request),
  - raise JobError(category, safe_message) for a categorized failure,
  - raise any other Exception → a generic 'handler_error' (only the exception type
    name is recorded — never str(e), which could contain content).

S24 ships one harmless `noop` job for validating enqueue → claim → run → terminal.
No real ingest/enrichment/backfill job is added yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


class JobCanceled(Exception):
    """Raised by a handler at a checkpoint when a cancel was requested."""


class JobError(Exception):
    """A categorized, content-free failure a handler can raise."""

    def __init__(self, category: str, message: str = "") -> None:
        super().__init__(category)
        self.category = category
        self.safe_message = str(message)[:200]


@dataclass
class JobResult:
    status: str = "succeeded"  # or 'partially_succeeded'
    summary: str | None = None
    progress: dict | None = None


@dataclass
class JobContext:
    job_id: str
    params: dict
    _db: object  # sqlalchemy Session (avoid import cycle)
    _cancel_check: Callable[[str], bool] = field(default=lambda _job_id: False)
    _progress: Callable[[str, dict], None] = field(default=lambda _job_id, _p: None)

    def check_canceled(self) -> None:
        if self._cancel_check(self.job_id):
            raise JobCanceled()

    def progress(self, **fields) -> None:
        self._progress(self.job_id, fields)


JobHandler = Callable[[JobContext], "JobResult | None"]

_HANDLERS: dict[str, JobHandler] = {}


def register(job_type: str):
    def deco(fn: JobHandler) -> JobHandler:
        _HANDLERS[job_type] = fn
        return fn
    return deco


def get_handler(job_type: str) -> JobHandler | None:
    return _HANDLERS.get(job_type)


def is_registered(job_type: str) -> bool:
    return job_type in _HANDLERS


# Job types a creator may enqueue via the API in S24 (infra validation only).
API_ENQUEUABLE = {"noop"}


@register("noop")
def _noop(ctx: JobContext) -> JobResult:
    """Harmless validation job. `params` (safe) control the outcome:
    {"fail": true} → failure; {"partial": true} → partial success; else success.
    Honors cancellation at a checkpoint."""
    ctx.progress(phase="running", steps_done=0, steps_total=1)
    ctx.check_canceled()
    if ctx.params.get("fail"):
        raise JobError("noop_failed", "requested failure")
    if ctx.params.get("partial"):
        return JobResult(
            status="partially_succeeded",
            summary="1 of 2 units done",
            progress={"phase": "done", "done": 1, "failed": 1, "total": 2},
        )
    ctx.progress(phase="done", steps_done=1, steps_total=1)
    return JobResult(summary="noop complete")
