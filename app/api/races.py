"""Race CRUD + calibration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.models import get_db
from app.models.tables import Race
from app.schemas import (
    RaceCreate,
    RaceOut,
    CalibrationIn,
    CalibrationOut,
    AutoDetectIn,
)

router = APIRouter(prefix="/races", tags=["races"])


def _default_calibration(width: int = 1280, height: int = 720) -> dict:
    """Return sensible default calibration values for a given frame resolution.

    Defaults
    --------
    finish_line  – a vertical line at the horizontal centre of the frame,
                   spanning its full height.  This is the most common
                   placement for a side-on finish camera.
    roi_polygon  – the full frame rectangle.  Narrows detector search to a
                   meaningful area; replace with the actual track corridor.
    reading_zone – None (OCR will fall back to the whole detection crop).
    """
    cx = width // 2
    return {
        "finish_line": [[cx, 0], [cx, height]],
        "roi_polygon": [[0, 0], [width, 0], [width, height], [0, height]],
        "reading_zone": None,
    }


# ── Races ───────────────────────────────────────────────────────────

@router.get("", response_model=list[RaceOut])
def list_races(db: Session = Depends(get_db)):
    """Return all races ordered by creation date descending."""
    from sqlalchemy import desc
    return db.query(Race).order_by(desc(Race.created_at)).all()


@router.post("", response_model=RaceOut, status_code=201)
def create_race(
    body: RaceCreate,
    db: Session = Depends(get_db),
    width: int = Query(1280, ge=160, le=7680, description="Camera frame width for default calibration"),
    height: int = Query(720, ge=90, le=4320, description="Camera frame height for default calibration"),
):
    """Create a race and pre-populate calibration with sensible defaults.

    Pass ``?width=1920&height=1080`` (or any resolution) to generate
    defaults for your actual camera.  Calibration can always be
    updated later via POST /races/{id}/calibration.
    """
    calib = _default_calibration(width, height)
    race = Race(
        name=body.name,
        finish_line=calib["finish_line"],
        roi_polygon=calib["roi_polygon"],
        reading_zone=calib["reading_zone"],
    )
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


@router.post("/{race_id}/calibration/auto-detect", response_model=CalibrationOut)
def auto_detect_calibration(
    race_id: str,
    body: AutoDetectIn,
    db: Session = Depends(get_db),
):
    """Run LSD-based finish-line detection on a video frame and optionally save.

    The server opens *video_path* (relative to the working directory; a
    leading ``/`` is stripped), samples ``frame_no``, detects the best
    near-horizontal (or near-vertical) line segment, and—if ``save=True``—
    persists it as the race's ``finish_line``.
    """
    import os
    from app.core.linefinder import auto_calibrate_from_video

    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")

    # Normalise path: strip leading slash so relative paths work from cwd
    video_path = body.video_path.lstrip("/")
    if not os.path.isfile(video_path):
        raise HTTPException(422, f"Video file not found on server: {video_path!r}")

    roi = tuple(body.roi_xywh) if body.roi_xywh else None
    result = auto_calibrate_from_video(
        video_path,
        frame_no=body.frame_no,
        roi_xywh=roi,
        orientation=body.orientation,
        angle_thresh_deg=body.angle_thresh_deg,
        expected_y_frac=tuple(body.expected_y_frac),
    )

    if result is None:
        raise HTTPException(
            422,
            "Could not detect a finish line in the specified frame. "
            "Try adjusting angle_thresh_deg, expected_y_frac, or roi_xywh.",
        )

    finish_line = result["finish_line"]

    if body.save:
        race.finish_line = finish_line
        db.commit()
        db.refresh(race)

    return CalibrationOut(
        roi_polygon=race.roi_polygon,
        finish_line=finish_line,
        reading_zone=race.reading_zone,
    )


@router.post("/{race_id}/calibration/reset", response_model=CalibrationOut)
def reset_calibration(
    race_id: str,
    db: Session = Depends(get_db),
    width: int = Query(1280, ge=160, le=7680, description="Camera frame width"),
    height: int = Query(720, ge=90, le=4320, description="Camera frame height"),
):
    """Reset calibration to sensible defaults for the given resolution.

    Use ``?width=1920&height=1080`` to match your actual camera resolution.
    The finish line is placed at the horizontal centre; the ROI covers the
    full frame.  Adjust from there using POST /races/{id}/calibration.
    """
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")
    calib = _default_calibration(width, height)
    race.finish_line = calib["finish_line"]
    race.roi_polygon = calib["roi_polygon"]
    race.reading_zone = calib["reading_zone"]
    db.commit()
    db.refresh(race)
    return CalibrationOut(
        roi_polygon=race.roi_polygon,
        finish_line=race.finish_line,
        reading_zone=race.reading_zone,
    )
