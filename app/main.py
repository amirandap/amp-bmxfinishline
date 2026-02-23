"""FastAPI application entry-point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.models import init_db
from app.api.races import router as races_router
from app.api.processing import router as processing_router
from app.api.arrivals import router as arrivals_router
from app.api.export import router as export_router
from app.api.upload import router as upload_router
from app.api.ws import router as ws_router
from app.api.feedback import router as feedback_router
from app.api.frontend import router as frontend_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    yield
    # Shutdown – nothing to clean up


app = FastAPI(
    title="Grassroot Finishline Backend",
    version="1.0.0",
    description=(
        "Bicycle race finish-line detection with OCR bib reading.\n\n"
        "**Backend** runs on port 8000 (pure API + WebSocket).\n"
        "**Frontend** is served from `/` (built Vite SPA) or separately via "
        "`npm run dev` on port 5173 during development."
    ),
    lifespan=lifespan,
)

# ── CORS ────────────────────────────────────────────────────────────
# Allow the Vite dev server (port 5173) and any configured origins to call
# the API.  In production set the FINISHLINE_CORS_ORIGINS env var to a
# comma-separated list of allowed origins.
import os as _os

_raw_origins = _os.getenv("FINISHLINE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
_allow_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    # Convenience: allow any localhost port in development.
    # Remove or restrict this regex in production deployments.
    allow_origin_regex=r"https?://localhost(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files (optional – present when Vite build output exists) ──
# Remove this mount (and set CORS above to your CDN origin) to run the
# frontend as a fully independent process on a separate host/port.
_static_dir = Path("app/frontend/static")
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")

# Serve local video files for calibration tool (omitted in production if dir absent)
_videos_dir = Path("videos")
if _videos_dir.exists():
    app.mount("/videos", StaticFiles(directory=str(_videos_dir)), name="videos")

# ── API routers ──────────────────────────────────────────────────────
app.include_router(races_router)
app.include_router(processing_router)
app.include_router(arrivals_router)
app.include_router(export_router)
app.include_router(upload_router)
app.include_router(ws_router)
app.include_router(feedback_router)
app.include_router(frontend_router)


@app.get("/health")
def health():
    return {"status": "ok"}
