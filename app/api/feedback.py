"""Feedback loop – stores confirmed bib crops and computes a gallery-based
second-confidence score using colour-histogram similarity.

Workflow
--------
1. When an arrival is confirmed (AUTO_OK / MANUAL_OK) the frontend / backend
   calls ``POST /races/{race_id}/arrivals/{arrival_id}/feedback`` which:
   a. Saves the OCR crop to ``data/outputs/{race_id}/feedback/{bib}/``
   b. Computes a compact HSV colour histogram (the *feature vector*).
   c. Persists a ``BibFeedback`` row.
2. During processing ``compute_gallery_confidence`` is called after OCR:
   it compares the current crop against stored exemplars for the same bib and
   returns a cosine-similarity score (0–1).  A high score adds confidence that
   the OCR read the correct bib.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import get_db
from app.models.tables import Arrival, BibFeedback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/races/{race_id}/arrivals/{arrival_id}", tags=["feedback"])


# ── Feature extraction ─────────────────────────────────────────────

def _compute_feature(image: np.ndarray) -> list[float]:
    """Return a compact 192-bin HSV colour histogram as a Python list.

    Bin layout (total = 192, L2-normalised):
      H channel: 90 bins over [0, 180) – full hue wheel at 2° resolution
      S channel: 64 bins over [0, 256) – saturation at ~4-unit resolution
      V channel: 38 bins over [0, 256) – value at ~6.7-unit resolution

    This is lightweight, deterministic, and works without any ML dependencies.
    """
    import cv2

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h_hist = cv2.calcHist([hsv], [0], None, [90],  [0, 180]).flatten()
    s_hist = cv2.calcHist([hsv], [1], None, [64],  [0, 256]).flatten()
    v_hist = cv2.calcHist([hsv], [2], None, [38],  [0, 256]).flatten()
    feat = np.concatenate([h_hist, s_hist, v_hist]).astype(np.float32)
    norm = np.linalg.norm(feat)
    if norm > 0:
        feat = feat / norm
    return feat.tolist()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two L2-normalised vectors (0–1)."""
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    return float(np.dot(va, vb))


# ── Gallery matching ───────────────────────────────────────────────

def compute_gallery_confidence(
    image: np.ndarray,
    bib: str,
    race_id: str,
    db: Session,
) -> float | None:
    """Compare *image* against stored exemplars for *bib* in this race.

    Returns the mean top-3 cosine similarity (0–1) or ``None`` if no
    exemplars exist yet.
    """
    exemplars = (
        db.query(BibFeedback)
        .filter(BibFeedback.race_id == race_id, BibFeedback.bib == bib)
        .all()
    )
    if not exemplars:
        return None

    query_feat = _compute_feature(image)

    sims: list[float] = []
    for ex in exemplars:
        if not ex.feature_vector:
            continue
        sims.append(_cosine_similarity(query_feat, ex.feature_vector))

    if not sims:
        return None

    sims.sort(reverse=True)
    top3 = sims[:3]
    return round(float(np.mean(top3)), 4)


# ── Endpoint ───────────────────────────────────────────────────────

@router.post("/feedback", status_code=201, summary="Confirm a bib match and add to gallery")
def submit_feedback(
    race_id: str,
    arrival_id: str,
    db: Session = Depends(get_db),
):
    """Confirm the bib on an arrival and add its crop to the gallery.

    Must be called after setting the arrival to ``MANUAL_OK`` or ``AUTO_OK``.
    The endpoint:
    - reads the crop from the burst directory
    - computes a feature vector
    - saves the crop to ``data/outputs/{race_id}/feedback/{bib}/``
    - persists a ``BibFeedback`` row
    - updates ``arrival.gallery_confidence`` with a fresh gallery score
    """
    import cv2

    arrival: Arrival | None = db.get(Arrival, arrival_id)
    if not arrival or arrival.race_id != race_id:
        raise HTTPException(404, "Arrival not found")

    if not arrival.bib:
        raise HTTPException(400, "Arrival has no bib set – confirm the bib first")

    if arrival.status not in ("AUTO_OK", "MANUAL_OK"):
        raise HTTPException(
            400,
            "Only confirmed arrivals (AUTO_OK / MANUAL_OK) can be used as feedback",
        )

    # ── Load the crossing-frame image ──────────────────────────────
    img_path = arrival.crossing_frame_path
    if not img_path or not os.path.isfile(img_path):
        raise HTTPException(404, "Crossing frame image not found on disk")

    image = cv2.imread(img_path)
    if image is None:
        raise HTTPException(422, "Could not decode crossing frame image")

    # ── Save crop to feedback directory ────────────────────────────
    cfg = get_config()
    feedback_dir = Path(cfg.output.base_dir) / race_id / "feedback" / arrival.bib
    feedback_dir.mkdir(parents=True, exist_ok=True)
    crop_path = str(feedback_dir / f"{arrival_id}.jpg")
    cv2.imwrite(crop_path, image)

    # ── Feature vector ─────────────────────────────────────────────
    feat = _compute_feature(image)

    # ── Persist feedback row (avoid duplicates for the same arrival) ─
    existing = (
        db.query(BibFeedback)
        .filter(BibFeedback.arrival_id == arrival_id)
        .first()
    )
    if existing:
        existing.feature_vector = feat
        existing.crop_path = crop_path
    else:
        db.add(BibFeedback(
            race_id=race_id,
            arrival_id=arrival_id,
            bib=arrival.bib,
            crop_path=crop_path,
            feature_vector=feat,
        ))

    # ── Recompute gallery confidence for this arrival ──────────────
    gallery_conf = compute_gallery_confidence(image, arrival.bib, race_id, db)
    arrival.gallery_confidence = gallery_conf

    db.commit()

    return {
        "arrival_id": arrival_id,
        "bib": arrival.bib,
        "gallery_confidence": gallery_conf,
        "crop_path": crop_path,
        "message": "Feedback accepted – gallery updated",
    }


@router.get("/feedback", summary="Retrieve gallery exemplars for this arrival's bib")
def get_feedback(
    race_id: str,
    arrival_id: str,
    db: Session = Depends(get_db),
):
    """Return the number of gallery exemplars stored for the bib on this arrival."""
    arrival: Arrival | None = db.get(Arrival, arrival_id)
    if not arrival or arrival.race_id != race_id:
        raise HTTPException(404, "Arrival not found")

    if not arrival.bib:
        return {"bib": None, "exemplar_count": 0}

    count = (
        db.query(BibFeedback)
        .filter(BibFeedback.race_id == race_id, BibFeedback.bib == arrival.bib)
        .count()
    )
    return {
        "bib": arrival.bib,
        "exemplar_count": count,
        "gallery_confidence": arrival.gallery_confidence,
    }
