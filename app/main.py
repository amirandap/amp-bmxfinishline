"""FastAPI application entry-point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.models import init_db
from app.api.races import router as races_router
from app.api.processing import router as processing_router
from app.api.arrivals import router as arrivals_router
from app.api.export import router as export_router
from app.api.upload import router as upload_router

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


# TODO [CHORE]: Decouple the frontend from the backend completely so they run
#   as fully independent processes on different ports with separate startups:
#
#   Backend  → uvicorn (port 8000) — pure API, no StaticFiles mount needed.
#   Frontend → Vite dev server (port 5173) in development,
#              *or* any static host (Nginx, Vercel, Netlify, GitHub Pages) in
#              production that just needs CORS access to the API.
#
#   Steps required:
#   1. Add CORS middleware to FastAPI (fastapi.middleware.cors.CORSMiddleware)
#      with the allowed origin(s) for the frontend host.
#   2. Remove the `StaticFiles` mount and the `frontend_router` import below.
#   3. Remove `app/api/frontend.py`, `app/frontend/static/` from the repo
#      (the built artefacts become CI/CD output, not source-controlled).
#   4. Update `vite.config.js`: keep the dev proxy for local work; set
#      `base` to '/' (or the CDN prefix) instead of '/static/';
#      point VITE_API_BASE env-var to the backend URL in production.
#   5. Update tasks.json / README so `Backend: start` and `Frontend: dev`
#      remain independent one-command startups with no shared file system
#      dependency between them.
#
#   Until this is done the static folder must be manually synced with the
#   Vite build output, which causes the current compatibility friction.

from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(
    title="Grassroot Finishline Backend",
    version="1.0.0",
    description="Bicycle race finish-line detection with OCR bib reading",
    lifespan=lifespan,
)

# Serve Vite build output (index.html, assets/) from /static
app.mount("/static", StaticFiles(directory="app/frontend/static", html=True), name="static")

# Serve local video files for calibration tool (omitted in production if dir absent)
_videos_dir = Path("videos")
if _videos_dir.exists():
    app.mount("/videos", StaticFiles(directory=str(_videos_dir)), name="videos")

app.include_router(races_router)
app.include_router(processing_router)
app.include_router(arrivals_router)
app.include_router(export_router)
app.include_router(upload_router)
# include web UI router
from app.api.frontend import router as frontend_router
app.include_router(frontend_router)


@app.get("/health")
def health():
    return {"status": "ok"}
