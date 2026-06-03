"""FastAPI application entrypoint (spec 05)."""
from __future__ import annotations

from fastapi import FastAPI

from .routers import network_map

app = FastAPI(title="Email Knowledge Continuity API")
app.include_router(network_map.router, prefix="/api")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
