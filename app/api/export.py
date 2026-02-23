"""Export endpoints – CSV and JSON."""

from __future__ import annotations

import csv
import io
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session

from app.models import get_db
from app.models.tables import Race, Arrival

router = APIRouter(prefix="/races/{race_id}", tags=["export"])


def _effective_position(a: Arrival) -> int:
    return a.manual_position if a.manual_position is not None else (a.auto_position or 9999)


def _sorted_arrivals(db: Session, race_id: str) -> list[Arrival]:
    arrivals = (
        db.query(Arrival)
        .filter(Arrival.race_id == race_id, Arrival.status != "MERGED")
        .all()
    )
    arrivals.sort(key=_effective_position)
    return arrivals


@router.get("/export.csv")
def export_csv(race_id: str, db: Session = Depends(get_db)):
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")

    arrivals = _sorted_arrivals(db, race_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "position", "bib", "timestamp_ms", "confidence", "status",
        "auto_position", "manual_position", "arrival_id",
    ])
    for i, a in enumerate(arrivals, 1):
        writer.writerow([
            i, a.bib or "", f"{a.timestamp_ms:.1f}", f"{a.confidence:.4f}",
            a.status, a.auto_position, a.manual_position or "", a.id,
        ])

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={race_id}_results.csv"},
    )


@router.get("/export.json")
def export_json(race_id: str, db: Session = Depends(get_db)):
    race = db.get(Race, race_id)
    if not race:
        raise HTTPException(404, "Race not found")

    arrivals = _sorted_arrivals(db, race_id)
    data = {
        "race_id": race.id,
        "race_name": race.name,
        "results": [
            {
                "position": i,
                "bib": a.bib,
                "timestamp_ms": a.timestamp_ms,
                "confidence": a.confidence,
                "status": a.status,
                "auto_position": a.auto_position,
                "manual_position": a.manual_position,
                "arrival_id": a.id,
            }
            for i, a in enumerate(arrivals, 1)
        ],
    }
    return JSONResponse(content=data)
