"""FastAPI application entrypoint (spec 05)."""
from __future__ import annotations

from fastapi import FastAPI

from .routers import cover_for_me, network_map, project_view, synthesis

app = FastAPI(title="Email Knowledge Continuity API")
app.include_router(network_map.router, prefix="/api")
app.include_router(project_view.router, prefix="/api")
app.include_router(synthesis.router, prefix="/api")
app.include_router(cover_for_me.router, prefix="/api")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
