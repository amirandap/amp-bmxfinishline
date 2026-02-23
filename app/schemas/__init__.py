"""Pydantic request / response schemas."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, field_serializer


# ── Race ────────────────────────────────────────────────────────────

class RaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)


class RaceOut(BaseModel):
    id: str
    name: str
    created_at: Optional[Union[str, datetime.datetime]] = None
    roi_polygon: Optional[List[List[float]]] = None
    finish_line: Optional[List[List[float]]] = None
    reading_zone: Optional[List[List[float]]] = None

    @field_serializer("created_at")
    @classmethod
    def serialize_created_at(cls, v):
        if isinstance(v, datetime.datetime):
            return v.isoformat()
        return v

    class Config:
        from_attributes = True


# ── Calibration ─────────────────────────────────────────────────────

class CalibrationIn(BaseModel):
    roi_polygon: List[List[float]] = Field(..., min_length=3, description="Polygon ROI as [[x,y], ...]")
    finish_line: List[List[float]] = Field(..., min_length=2, max_length=2, description="Finish line [[x1,y1],[x2,y2]]")
    reading_zone: Optional[List[List[float]]] = Field(None, description="Optional bib reading zone polygon")


class CalibrationOut(BaseModel):
    roi_polygon: Optional[List[List[float]]] = None
    finish_line: Optional[List[List[float]]] = None
    reading_zone: Optional[List[List[float]]] = None


class AutoDetectIn(BaseModel):
    """Request body for the POST /calibration/auto-detect endpoint."""
    video_path: str = Field(
        ...,
        description="Server-side path to the video file (e.g. 'videos/clip.MOV')."
        "  A leading '/' is stripped automatically.",
    )
    frame_no: int = Field(0, ge=0, description="Frame index to sample (0 = first frame)")
    orientation: str = Field(
        "horizontal",
        pattern="^(horizontal|vertical)$",
        description="Expected finish-line orientation in the frame",
    )
    angle_thresh_deg: float = Field(
        15.0, ge=1.0, le=45.0,
        description="Max deviation from target axis to consider a segment (degrees)",
    )
    expected_y_frac: List[float] = Field(
        [0.2, 0.9],
        min_length=2, max_length=2,
        description="[lo, hi] vertical band (0–1) where the finish line is expected",
    )
    roi_xywh: Optional[List[int]] = Field(
        None, min_length=4, max_length=4,
        description="Crop region [x, y, w, h] in pixels; None = auto (right-60 %×bottom-60 %)",
    )
    save: bool = Field(True, description="Persist the detected finish line to the race record")


# ── Processing ──────────────────────────────────────────────────────

class ProcessStartIn(BaseModel):
    input_type: str = Field(..., pattern="^(file|rtmp)$")
    input: str = Field(..., min_length=1)
    options: Optional[Dict[str, Any]] = None


class ProcessStatusOut(BaseModel):
    state: str  # idle | running | stopping | finished | error
    frames_processed: int = 0
    arrivals_detected: int = 0
    error: Optional[str] = None
    camera_moved: bool = False


# ── Arrival ─────────────────────────────────────────────────────────

class ArrivalOut(BaseModel):
    id: str
    race_id: str
    track_id: Optional[int] = None
    timestamp_ms: float
    bib: Optional[str] = None
    confidence: float = 0.0
    candidates: Optional[List[Dict[str, Any]]] = None
    status: str
    auto_position: Optional[int] = None
    manual_position: Optional[int] = None
    crossing_frame_path: Optional[str] = None
    burst_dir: Optional[str] = None
    merged_into: Optional[str] = None
    effective_position: Optional[int] = None
    gallery_confidence: Optional[float] = None

    class Config:
        from_attributes = True


class ArrivalPatch(BaseModel):
    bib: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(MANUAL_OK|NEEDS_REVIEW|AUTO_OK)$")


class ReorderIn(BaseModel):
    arrival_ids_in_order: List[str]


class MergeIn(BaseModel):
    keep_id: str
    merge_id: str
    bib: Optional[str] = None
