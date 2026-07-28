"""FastAPI application entrypoint (spec 05)."""
from __future__ import annotations

from fastapi import FastAPI

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
    network_map,
    oauth_gmail,
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

app = FastAPI(title="Email Knowledge Continuity API")
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
