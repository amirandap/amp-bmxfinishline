"""Arrivals CRUD + manual correction endpoints."""

from __future__ import annotations

import datetime
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import asc

from app.models import get_db
from app.models.tables import Race, Arrival, AuditLog
from app.schemas import ArrivalOut, ArrivalPatch, ReorderIn, MergeIn

router = APIRouter(prefix="/races/{race_id}/arrivals", tags=["arrivals"])


def _effective_position(a: Arrival) -> int:
    return a.manual_position if a.manual_position is not None else (a.auto_position or 9999)


def _arrivals_sorted(db: Session, race_id: str) -> list[Arrival]:
    arrivals = (
        db.query(Arrival)
        .filter(Arrival.race_id == race_id, Arrival.status != "MERGED")
        .all()
    )
    arrivals.sort(key=_effective_position)
    return arrivals


def _to_out(a: Arrival) -> ArrivalOut:
    return ArrivalOut(
        id=a.id,
        race_id=a.race_id,
        track_id=a.track_id,
        timestamp_ms=a.timestamp_ms,
        bib=a.bib,
        confidence=a.confidence,
        candidates=a.candidates,
        status=a.status,
        auto_position=a.auto_position,
        manual_position=a.manual_position,
        crossing_frame_path=a.crossing_frame_path,
        burst_dir=a.burst_dir,
        merged_into=a.merged_into,
        effective_position=_effective_position(a),
    )


# ── List arrivals ──────────────────────────────────────────────────

@router.get("", response_model=list[ArrivalOut])
def list_arrivals(race_id: str, db: Session = Depends(get_db)):
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")
    return [_to_out(a) for a in _arrivals_sorted(db, race_id)]

# ── Delete arrival ────────────────────────────────────────────

@router.delete("/{arrival_id}", status_code=204)
def delete_arrival(race_id: str, arrival_id: str, db: Session = Depends(get_db)):
    """Hard-delete an arrival record."""
    arrival = db.get(Arrival, arrival_id)
    if not arrival or arrival.race_id != race_id:
        raise HTTPException(404, "Arrival not found")
    db.add(AuditLog(
        race_id=race_id,
        arrival_id=arrival_id,
        action="delete_arrival",
        detail={"bib": arrival.bib, "position": arrival.auto_position},
    ))
    db.delete(arrival)
    db.commit()


# ── Serve crossing frame image ─────────────────────────────────

@router.get("/{arrival_id}/image")
def get_arrival_image(race_id: str, arrival_id: str, db: Session = Depends(get_db)):
    """Return the crossing-frame JPEG for a given arrival."""
    arrival = db.get(Arrival, arrival_id)
    if not arrival or arrival.race_id != race_id:
        raise HTTPException(404, "Arrival not found")
    path = arrival.crossing_frame_path
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Image not available")
    return FileResponse(path, media_type="image/jpeg")

# ── Patch arrival (edit bib / status) ──────────────────────────────

@router.patch("/{arrival_id}", response_model=ArrivalOut)
def patch_arrival(race_id: str, arrival_id: str, body: ArrivalPatch, db: Session = Depends(get_db)):
    arrival = db.get(Arrival, arrival_id)
    if not arrival or arrival.race_id != race_id:
        raise HTTPException(404, "Arrival not found")

    changes: dict = {}
    if body.bib is not None:
        changes["bib_old"] = arrival.bib
        arrival.bib = body.bib
        changes["bib_new"] = body.bib
    if body.status is not None:
        changes["status_old"] = arrival.status
        arrival.status = body.status
        changes["status_new"] = body.status

    if changes:
        db.add(AuditLog(
            race_id=race_id,
            arrival_id=arrival_id,
            action="patch_arrival",
            detail=changes,
        ))
    db.commit()
    db.refresh(arrival)
    return _to_out(arrival)


# ── Reorder ────────────────────────────────────────────────────────

@router.post("/reorder", response_model=list[ArrivalOut])
def reorder_arrivals(race_id: str, body: ReorderIn, db: Session = Depends(get_db)):
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")

    for pos, aid in enumerate(body.arrival_ids_in_order, start=1):
        arrival = db.get(Arrival, aid)
        if not arrival or arrival.race_id != race_id:
            raise HTTPException(404, f"Arrival {aid} not found in race")
        arrival.manual_position = pos

    db.add(AuditLog(
        race_id=race_id,
        action="reorder",
        detail={"order": body.arrival_ids_in_order},
    ))
    db.commit()
    return [_to_out(a) for a in _arrivals_sorted(db, race_id)]


# ── Merge ──────────────────────────────────────────────────────────

@router.post("/merge", response_model=ArrivalOut)
def merge_arrivals(race_id: str, body: MergeIn, db: Session = Depends(get_db)):
    keep = db.get(Arrival, body.keep_id)
    merge = db.get(Arrival, body.merge_id)
    if not keep or keep.race_id != race_id:
        raise HTTPException(404, "keep arrival not found")
    if not merge or merge.race_id != race_id:
        raise HTTPException(404, "merge arrival not found")

    # Keep earliest timestamp
    if merge.timestamp_ms < keep.timestamp_ms:
        keep.timestamp_ms = merge.timestamp_ms

    if body.bib is not None:
        keep.bib = body.bib
    keep.status = "MANUAL_OK"

    merge.status = "MERGED"
    merge.merged_into = keep.id

    db.add(AuditLog(
        race_id=race_id,
        arrival_id=keep.id,
        action="merge",
        detail={"merged_id": merge.id, "bib": keep.bib},
    ))
    db.commit()
    db.refresh(keep)
    return _to_out(keep)
