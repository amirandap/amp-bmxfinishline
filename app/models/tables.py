"""SQLAlchemy ORM table definitions."""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Text,
    DateTime,
    ForeignKey,
    JSON,
    Enum as SAEnum,
)
from sqlalchemy.orm import relationship

from app.models import Base


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


# ── Race ────────────────────────────────────────────────────────────

class Race(Base):
    __tablename__ = "races"

    id: str = Column(String(12), primary_key=True, default=_uuid)
    name: str = Column(String(256), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # calibration stored as JSON
    roi_polygon = Column(JSON, nullable=True)       # [[x,y], ...]
    finish_line = Column(JSON, nullable=True)        # [[x1,y1],[x2,y2]]
    reading_zone = Column(JSON, nullable=True)       # [[x,y], ...] or null

    arrivals = relationship("Arrival", back_populates="race", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="race", cascade="all, delete-orphan")


# ── Arrival ─────────────────────────────────────────────────────────

class Arrival(Base):
    __tablename__ = "arrivals"

    id: str = Column(String(12), primary_key=True, default=_uuid)
    race_id: str = Column(String(12), ForeignKey("races.id"), nullable=False)
    track_id: int = Column(Integer, nullable=True)
    timestamp_ms: float = Column(Float, nullable=False)

    bib: Optional[str] = Column(String(8), nullable=True)
    confidence: float = Column(Float, default=0.0)
    candidates = Column(JSON, nullable=True)  # [{bib, score}, ...]

    status: str = Column(String(20), default="NEEDS_REVIEW")  # AUTO_OK | NEEDS_REVIEW | MANUAL_OK | MERGED

    auto_position: int = Column(Integer, nullable=True)
    manual_position: Optional[int] = Column(Integer, nullable=True)

    # Evidence paths
    crossing_frame_path: Optional[str] = Column(Text, nullable=True)
    burst_dir: Optional[str] = Column(Text, nullable=True)

    merged_into: Optional[str] = Column(String(12), nullable=True)

    # Second-opinion confidence score from per-bib gallery matcher (0–1, None = not computed)
    gallery_confidence: Optional[float] = Column(Float, nullable=True)

    race = relationship("Race", back_populates="arrivals")


# ── Bib feedback / gallery (training data for second-confidence score) ──

class BibFeedback(Base):
    """Stores confirmed bib-crop pairs for per-bib gallery matching.

    Every time a bib is confirmed (AUTO_OK / MANUAL_OK) the backend saves the
    OCR crop and a compact colour-histogram feature vector here.  Future OCR
    runs can then compare against this gallery to produce a *gallery_confidence*
    second-opinion score that improves with use.
    """

    __tablename__ = "bib_feedback"

    id: str = Column(String(12), primary_key=True, default=_uuid)
    race_id: str = Column(String(12), ForeignKey("races.id"), nullable=False)
    arrival_id: Optional[str] = Column(String(12), nullable=True)
    bib: str = Column(String(8), nullable=False, index=True)
    crop_path: Optional[str] = Column(Text, nullable=True)
    # Compact feature vector stored as a JSON list of floats (e.g. 256-bin histogram)
    feature_vector = Column(JSON, nullable=True)
    confirmed_at = Column(DateTime, default=datetime.datetime.utcnow)

    race = relationship("Race")


# ── Audit log ───────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    race_id: str = Column(String(12), ForeignKey("races.id"), nullable=False)
    arrival_id: Optional[str] = Column(String(12), nullable=True)
    action: str = Column(String(64), nullable=False)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    race = relationship("Race", back_populates="audit_logs")
