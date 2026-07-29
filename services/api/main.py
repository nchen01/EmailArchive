"""FastAPI application entrypoint (spec 05)."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# Load .env before any service module reads environment variables.
# This mirrors the pattern in CLI scripts (scripts/_env.py) so that running
# `uvicorn services.api.main:app` picks up VOYAGE_API_KEY / ANTHROPIC_API_KEY
# from .env without requiring the caller to export them manually.
# Only called here, never inside shared service modules (no import-time side effects).
from scripts._env import load_local_env
load_local_env()

from .routers import (
    cover_for_me,
    gmail_ingest,
    handoff,
    handoff_recipient,
    jobs,
    network_map,
    oauth_gmail,
    pipeline_jobs,
    preflight,
    project_view,
    relationship_map,
    source_message,
    synthesis,
)

# Redact OAuth code/state (and tokens) from uvicorn access logs before anything
# serves — Google's callback carries the authorization code in the query string
# (S23). Installed at import so it applies under any launcher.
from .log_redaction import install_access_log_redaction
install_access_log_redaction()

# Register all job handlers (noop, gmail_ingest_window, …) at startup so the API
# can enqueue them and the type check passes (S24/S25).
import services.jobs.handlers  # noqa: E402,F401

@asynccontextmanager
async def lifespan(app: FastAPI):
    """S27 hosted-readiness startup guard. No-op unless EKC_DEPLOY_ENV=production
    (so local dev and tests never trip it). In a hosted deployment, refuse to start
    on unsafe config (dev auth/vault, un-migrated DB, missing/localhost OAuth,
    missing log redaction, wildcard CORS, unreachable queue, recipient regression).
    The banner is safe metadata only — never a secret, token, or DB URL."""
    from services.hosted_readiness import HostedReadinessError, run_startup_guard

    try:
        run_startup_guard(component="api")
    except HostedReadinessError as exc:
        logging.getLogger("ekc.hosted").error(
            "HOSTED READINESS GUARD FAILED — refusing to start the API:\n  %s",
            exc.safe_summary(),
        )
        raise
    yield


app = FastAPI(title="Email Knowledge Continuity API", lifespan=lifespan)

# Cross-origin CORS is opt-in and never wildcard (S27 §9.4). Same-origin is the
# preferred default and installs no middleware. A wildcard (*) value is deliberately
# NOT honored here — the hosted-readiness check fails it instead.
_allowed_origins = [
    o.strip() for o in os.environ.get("EKC_ALLOWED_ORIGINS", "").split(",")
    if o.strip() and o.strip() != "*"
]
if _allowed_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(network_map.router, prefix="/api")
app.include_router(project_view.router, prefix="/api")
app.include_router(synthesis.router, prefix="/api")
app.include_router(cover_for_me.router, prefix="/api")
app.include_router(preflight.router, prefix="/api")
app.include_router(relationship_map.router, prefix="/api")
app.include_router(source_message.router, prefix="/api")
app.include_router(gmail_ingest.router, prefix="/api")
app.include_router(handoff.router, prefix="/api")
app.include_router(handoff_recipient.router, prefix="/api")
app.include_router(oauth_gmail.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(pipeline_jobs.router, prefix="/api")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# Mirror of /healthz under the /api prefix so the Vite dev proxy (which forwards
# only /api) and the frontend can cheaply probe backend reachability. The
# frontend pings this before the data tabs so "backend unavailable" is shown
# explicitly instead of each tab spinning independently.
@app.get("/api/health")
async def api_health() -> dict[str, str]:
    return {"status": "ok"}


# S27 readiness probe. Liveness stays /healthz (process up); /readyz reflects
# hosted READINESS (DB reachable + at migration head + auth-mode production +
# production vault + log redaction, in a hosted context). The public body is a
# BARE status string — no check names, config, secrets, tokens, DB URL, or counts —
# so an unauthenticated probe is not an info-leak. The detailed report is available
# only via `scripts/preflight.py --hosted`. In local dev it always reports ready.
async def _readyz_impl() -> JSONResponse:
    from services.hosted_readiness import evaluate_readiness

    try:
        ready, _ = evaluate_readiness()
    except Exception as exc:  # never leak; an evaluation error is itself not-ready
        # Safe-metadata posture (same as the rest of the product): log ONLY the
        # exception TYPE name — never a traceback (no exc_info) and never str(exc),
        # which could carry an env value, DB URL, or OAuth/secret detail.
        logging.getLogger("ekc.hosted").error(
            "readiness evaluation error (%s) — reporting degraded", type(exc).__name__
        )
        return JSONResponse(status_code=503, content={"status": "degraded"})
    if ready:
        return JSONResponse(status_code=200, content={"status": "ready"})
    return JSONResponse(status_code=503, content={"status": "degraded"})


@app.get("/readyz")
async def readyz() -> JSONResponse:
    return await _readyz_impl()


@app.get("/api/readyz")
async def api_readyz() -> JSONResponse:
    return await _readyz_impl()
