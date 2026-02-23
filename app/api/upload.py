"""Video upload endpoint – saves an uploaded file to disk for later processing."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import get_db
from app.models.tables import Race

router = APIRouter(prefix="/races/{race_id}/upload", tags=["upload"])


@router.post("", summary="Upload a video file for processing")
async def upload_video(
    race_id: str,
    file: UploadFile = File(..., description="MP4 / MOV video file"),
    db: Session = Depends(get_db),
):
    """
    Upload a video file and store it under
    ``data/outputs/{race_id}/uploads/``.

    Returns the server-side path that can be passed directly to
    ``POST /races/{race_id}/process/start`` as the ``input`` field.
    """
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")

    cfg = get_config()
    upload_dir = Path(cfg.output.base_dir) / race_id / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Sanitise filename – keep only the last path component to avoid
    # directory traversal attacks, then replace any unsafe chars.
    safe_name = Path(file.filename or "upload").name
    safe_name = safe_name.replace(" ", "_")
    dest = upload_dir / safe_name

    # If a file with the same name already exists, add a numeric suffix.
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        i = 1
        while dest.exists():
            dest = upload_dir / f"{stem}_{i}{suffix}"
            i += 1

    with dest.open("wb") as fout:
        shutil.copyfileobj(file.file, fout)

    return {
        "path": str(dest.resolve()),
        "filename": dest.name,
        "race_id": race_id,
    }
