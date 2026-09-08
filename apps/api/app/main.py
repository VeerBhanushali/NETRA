"""NETRA API — application entrypoint.

Run locally:
    uvicorn app.main:app --reload --app-dir apps/api --port 8000
Interactive docs at http://127.0.0.1:8000/docs
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routers import (admin, ai_router, alerts, config_router, device_router,
                      registry_router,
                      faces_router, ingest, stream, vehicles)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    config.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[netra] database ready at {config.DB_PATH}")
    print(f"[netra] auto-accept >= {config.AUTO_ACCEPT_CONFIDENCE}, "
          f"review >= {config.REVIEW_CONFIDENCE}")
    yield


app = FastAPI(
    title="NETRA — ANPR & Crime Tracking Platform",
    version="1.0.0",
    description=(
        "City-wide automatic number plate recognition with spatio-temporal "
        "crime detection. Plates are read across many frames and confirmed by "
        "temporal voting; anything below the auto-accept threshold goes to a "
        "human rather than being published as fact."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROUTERS = (ingest.router, vehicles.router, alerts.router, admin.router,
           stream.router, ai_router.router, config_router.router,
           faces_router.router, faces_router.scan_router,
           device_router.router, registry_router.router)

# Every route is served at BOTH /api/... and /api/v1/...
#
# SIH26127 §7 specifies a versioned surface. Mounting the same routers
# twice gives the contract the spec asks for without a flag-day rename of
# every existing caller — v1 is canonical, the unversioned path stays as
# a deprecated alias so the running dashboard does not break mid-demo.
for r in ROUTERS:
    # /api/v1 is the canonical, documented surface (SIH26127 §7).
    app.include_router(r, prefix="/api/v1")
    # /api stays as a deprecated alias so the running dashboard and the
    # edge workers keep working without a flag-day rename.
    app.include_router(r, prefix="/api", include_in_schema=False)

# Evidence crops. Served read-only; in a real deployment these would sit
# behind short-lived signed URLs with per-view access logging.
config.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/evidence", StaticFiles(directory=str(config.EVIDENCE_DIR)), name="evidence")


@app.get("/api/health", tags=["health"])
def health() -> JSONResponse:
    """Liveness plus enough state to diagnose a demo failure at a glance."""
    try:
        cameras = db.query_one("SELECT COUNT(*) AS n FROM cameras")["n"]
        sightings = db.query_one("SELECT COUNT(*) AS n FROM sightings")["n"]
    except Exception as exc:                          # noqa: BLE001
        return JSONResponse(status_code=503,
                            content={"ok": False, "error": repr(exc)})
    return JSONResponse({
        "ok": True,
        "database": str(config.DB_PATH),
        "cameras": cameras,
        "sightings": sightings,
        "ai_enabled": config.AI_ENABLED,
    })


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"service": "netra-api", "docs": "/docs", "health": "/api/health"}
