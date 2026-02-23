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


from fastapi.staticfiles import StaticFiles

app = FastAPI(
    title="Grassroot Finishline Backend",
    version="1.0.0",
    description="Bicycle race finish-line detection with OCR bib reading",
    lifespan=lifespan,
)

# Serve Vite build output (index.html, assets/) from /static
app.mount("/static", StaticFiles(directory="app/frontend/static", html=True), name="static")

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
