"""Processing control endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import get_db, get_session_factory
from app.models.tables import Race
from app.schemas import ProcessStartIn, ProcessStatusOut
from app.core.pipeline import get_pipeline, start_pipeline, stop_pipeline

router = APIRouter(prefix="/races/{race_id}/process", tags=["processing"])


@router.post("/start", response_model=ProcessStatusOut)
def process_start(race_id: str, body: ProcessStartIn, db: Session = Depends(get_db)):
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")
    if not race.finish_line or not race.roi_polygon:
        raise HTTPException(400, "Calibration must be set before processing")

    try:
        pipe = start_pipeline(
            race_id,
            get_session_factory(),
            body.input_type,
            body.input,
            body.options,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    return ProcessStatusOut(
        state=pipe.status.state,
        frames_processed=pipe.status.frames_processed,
        arrivals_detected=pipe.status.arrivals_detected,
        camera_moved=pipe.status.camera_moved,
    )


@router.post("/stop", response_model=ProcessStatusOut)
def process_stop(race_id: str):
    pipe = get_pipeline(race_id)
    if not pipe:
        raise HTTPException(404, "No pipeline for this race")
    stop_pipeline(race_id)
    return ProcessStatusOut(
        state=pipe.status.state,
        frames_processed=pipe.status.frames_processed,
        arrivals_detected=pipe.status.arrivals_detected,
        camera_moved=pipe.status.camera_moved,
    )


@router.get("/status", response_model=ProcessStatusOut)
def process_status(race_id: str):
    pipe = get_pipeline(race_id)
    if not pipe:
        return ProcessStatusOut(state="idle")
    return ProcessStatusOut(
        state=pipe.status.state,
        frames_processed=pipe.status.frames_processed,
        arrivals_detected=pipe.status.arrivals_detected,
        error=pipe.status.error,
        camera_moved=pipe.status.camera_moved,
    )
