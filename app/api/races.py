"""Race CRUD + calibration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import get_db
from app.models.tables import Race
from app.schemas import (
    RaceCreate,
    RaceOut,
    CalibrationIn,
    CalibrationOut,
)

router = APIRouter(prefix="/races", tags=["races"])


# ── Races ───────────────────────────────────────────────────────────

@router.get("", response_model=list[RaceOut])
def list_races(db: Session = Depends(get_db)):
    """Return all races ordered by creation date descending."""
    from sqlalchemy import desc
    return db.query(Race).order_by(desc(Race.created_at)).all()


@router.post("", response_model=RaceOut, status_code=201)
def create_race(body: RaceCreate, db: Session = Depends(get_db)):
    race = Race(name=body.name)
    db.add(race)
    db.commit()
    db.refresh(race)
    return race


@router.get("/{race_id}", response_model=RaceOut)
def get_race(race_id: str, db: Session = Depends(get_db)):
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")
    return race


@router.delete("/{race_id}", status_code=204)
def delete_race(race_id: str, db: Session = Depends(get_db)):
    """Delete a race and all its arrivals / audit logs (cascade)."""
    from app.core.pipeline import get_pipeline, stop_pipeline
    pipe = get_pipeline(race_id)
    if pipe and pipe.status.state == "running":
        stop_pipeline(race_id)
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")
    db.delete(race)
    db.commit()


# ── Calibration ─────────────────────────────────────────────────────

@router.get("/{race_id}/calibration", response_model=CalibrationOut)
def get_calibration(race_id: str, db: Session = Depends(get_db)):
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")
    return CalibrationOut(
        roi_polygon=race.roi_polygon,
        finish_line=race.finish_line,
        reading_zone=race.reading_zone,
    )


@router.post("/{race_id}/calibration", response_model=CalibrationOut)
def set_calibration(race_id: str, body: CalibrationIn, db: Session = Depends(get_db)):
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")

    # Validate finish line has exactly 2 points
    if len(body.finish_line) != 2:
        raise HTTPException(422, "finish_line must have exactly 2 points")
    for pt in body.finish_line:
        if len(pt) != 2:
            raise HTTPException(422, "Each finish_line point must be [x, y]")

    # Validate ROI polygon has >= 3 points
    if len(body.roi_polygon) < 3:
        raise HTTPException(422, "roi_polygon must have at least 3 points")
    for pt in body.roi_polygon:
        if len(pt) != 2:
            raise HTTPException(422, "Each roi_polygon point must be [x, y]")

    if body.reading_zone:
        for pt in body.reading_zone:
            if len(pt) != 2:
                raise HTTPException(422, "Each reading_zone point must be [x, y]")

    race.roi_polygon = body.roi_polygon
    race.finish_line = body.finish_line
    race.reading_zone = body.reading_zone
    db.commit()
    db.refresh(race)

    return CalibrationOut(
        roi_polygon=race.roi_polygon,
        finish_line=race.finish_line,
        reading_zone=race.reading_zone,
    )
