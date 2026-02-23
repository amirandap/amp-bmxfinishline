"""End-to-end processing pipeline – orchestrates ingest → detect → cross → burst → OCR → persist."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.config import get_config, AppConfig
# Heavy ML imports are done lazily inside _run() to avoid hard dependency at import time
from app.models.tables import Race, Arrival

logger = logging.getLogger(__name__)


@dataclass
class PipelineStatus:
    state: str = "idle"  # idle | running | stopping | finished | error
    frames_processed: int = 0
    arrivals_detected: int = 0
    error: str | None = None
    camera_moved: bool = False


# ── Camera movement detector ───────────────────────────────────────

class _CameraMovementDetector:
    """Detects significant camera displacement between sampled frames."""

    def __init__(self, check_interval: int, displacement_threshold: float):
        self._interval = max(1, check_interval)
        self._threshold = displacement_threshold
        self._prev_gray: np.ndarray | None = None

    def check(self, frame_index: int, image: np.ndarray) -> bool:
        """Return True if significant camera movement is detected on this frame."""
        if frame_index % self._interval != 0:
            return False
        import cv2
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (320, 180))  # work at reduced resolution
        moved = False
        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, gray)
            mean_displacement = float(diff.mean())
            if mean_displacement > self._threshold:
                logger.warning(
                    "Camera movement detected at frame %d (mean_diff=%.2f > threshold=%.2f)",
                    frame_index, mean_displacement, self._threshold,
                )
                moved = True
        self._prev_gray = gray
        return moved


class ProcessingPipeline:
    """Runs video processing for a single race in a background thread."""

    def __init__(self, race_id: str, session_factory):
        self._race_id = race_id
        self._session_factory = session_factory
        self._cfg: AppConfig = get_config()
        self._status = PipelineStatus()
        self._stop_flag = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def status(self) -> PipelineStatus:
        return self._status

    def start(self, input_type: str, input_path: str, options: dict | None = None) -> None:
        if self._status.state == "running":
            raise RuntimeError("Pipeline already running")
        self._stop_flag.clear()
        self._status = PipelineStatus(state="running")
        self._thread = threading.Thread(
            target=self._run,
            args=(input_type, input_path, options or {}),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._status.state == "running":
            self._status.state = "stopping"
            self._stop_flag.set()

    # ── Main loop ──────────────────────────────────────────────────

    def _run(self, input_type: str, input_path: str, options: dict) -> None:
        # Lazy imports – keeps API startable without ML deps installed
        from app.core.ingest import FrameSource, Frame, create_source
        from app.core.detection import Detector
        from app.core.crossing import CrossingDetector, FinishEvent
        from app.core.burst import BurstBuffer, save_burst
        from app.core.ocr import extract_bib

        source: FrameSource | None = None
        db: Session | None = None
        try:
            db = self._session_factory()
            race: Race | None = db.get(Race, self._race_id)
            if race is None:
                raise RuntimeError(f"Race {self._race_id} not found")

            if not race.finish_line or not race.roi_polygon:
                raise RuntimeError("Calibration (roi_polygon & finish_line) must be set before processing")

            roi_polygon: list[list[float]] = race.roi_polygon
            finish_line: list[list[float]] = race.finish_line
            reading_zone: list[list[float]] | None = race.reading_zone

            source = create_source(input_type, input_path)
            detector = Detector(self._cfg)
            crossing_det = CrossingDetector(
                finish_line,
                cooldown_seconds=self._cfg.crossing.cooldown_seconds,
                crossing_direction=self._cfg.crossing.crossing_direction,
                crossing_point=self._cfg.crossing.crossing_point,
                interpolate_timestamp=self._cfg.crossing.interpolate_timestamp,
            )
            burst_buf = BurstBuffer(self._cfg.burst.frames_before, self._cfg.burst.frames_after)
            cam_movement = _CameraMovementDetector(
                self._cfg.camera.check_interval,
                self._cfg.camera.displacement_threshold,
            )

            # Count existing arrivals to set auto_position correctly
            existing_count: int = (
                db.query(Arrival)
                .filter(Arrival.race_id == self._race_id, Arrival.status != "MERGED")
                .count()
            )
            next_position = existing_count + 1

            # Pending finish events waiting for burst completion
            pending_events: dict[int, FinishEvent] = {}

            for frame in source.frames():
                if self._stop_flag.is_set():
                    logger.info("Pipeline stop requested")
                    break

                self._status.frames_processed = frame.index + 1

                # Camera movement check
                if cam_movement.check(frame.index, frame.image):
                    self._status.camera_moved = True

                # Detection + tracking
                detections = detector.detect_and_track(frame.image, roi_polygon)

                # Crossing detection
                events = crossing_det.update(detections, frame.timestamp_ms, frame.index)
                for ev in events:
                    burst_buf.trigger(ev)
                    pending_events[ev.track_id] = ev

                # Push frame into burst buffer; check for completed bursts
                completed = burst_buf.push(frame)

                for ev, burst_frames in completed:
                    self._process_finish(ev, burst_frames, db, race, reading_zone, next_position)
                    next_position += 1
                    pending_events.pop(ev.track_id, None)

            # Flush remaining bursts
            for ev, burst_frames in burst_buf.flush_all():
                self._process_finish(ev, burst_frames, db, race, reading_zone, next_position)
                next_position += 1

            self._status.state = "finished"
            logger.info(
                "Pipeline finished  frames=%d  arrivals=%d",
                self._status.frames_processed, self._status.arrivals_detected,
            )
            # Broadcast final status
            try:
                from app.api.ws import manager as ws_manager
                ws_manager.broadcast_sync(self._race_id, {
                    "type": "status",
                    "state": "finished",
                    "frames_processed": self._status.frames_processed,
                    "arrivals_detected": self._status.arrivals_detected,
                    "camera_moved": self._status.camera_moved,
                })
            except Exception:
                pass

        except Exception as e:
            logger.exception("Pipeline error")
            self._status.state = "error"
            self._status.error = str(e)
            try:
                from app.api.ws import manager as ws_manager
                ws_manager.broadcast_sync(self._race_id, {
                    "type": "error",
                    "message": str(e),
                })
            except Exception:
                pass
        finally:
            if source:
                source.release()
            if db:
                db.close()

    def _process_finish(
        self,
        event,       # FinishEvent
        burst_frames, # list[Frame]
        db: Session,
        race: Race,
        reading_zone,
        position: int,
    ) -> None:
        """Process a single finish event: save burst, run OCR, persist arrival."""
        from app.core.burst import save_burst
        from app.core.ocr import extract_bib
        from app.api.ws import manager as ws_manager

        # Save burst evidence
        crossing_path, burst_dir = save_burst(
            event, burst_frames, self._cfg.output.base_dir, self._race_id,
        )

        # OCR
        ocr_result = extract_bib(
            burst_frames, event.detection, self._cfg.ocr, reading_zone,
        )

        status = "AUTO_OK" if ocr_result.confidence >= self._cfg.ocr.confidence_threshold else "NEEDS_REVIEW"

        arrival = Arrival(
            race_id=self._race_id,
            track_id=event.track_id,
            timestamp_ms=event.timestamp_ms,
            bib=ocr_result.bib,
            confidence=ocr_result.confidence,
            candidates=ocr_result.candidates,
            status=status,
            auto_position=position,
            crossing_frame_path=crossing_path,
            burst_dir=burst_dir,
        )
        db.add(arrival)
        db.commit()
        self._status.arrivals_detected += 1

        # Broadcast new arrival over WebSocket
        ws_manager.broadcast_sync(self._race_id, {
            "type": "arrival",
            "id": arrival.id,
            "bib": arrival.bib,
            "confidence": arrival.confidence,
            "position": position,
            "timestamp_ms": arrival.timestamp_ms,
            "status": arrival.status,
        })

        logger.info(
            "Arrival persisted  id=%s  bib=%s  pos=%d  status=%s",
            arrival.id, arrival.bib, position, status,
        )


# ── Global pipeline registry (one per race) ────────────────────────

_pipelines: dict[str, ProcessingPipeline] = {}
_lock = threading.Lock()


def get_pipeline(race_id: str) -> ProcessingPipeline | None:
    return _pipelines.get(race_id)


def start_pipeline(race_id: str, session_factory, input_type: str, input_path: str, options: dict | None = None) -> ProcessingPipeline:
    with _lock:
        existing = _pipelines.get(race_id)
        if existing and existing.status.state == "running":
            raise RuntimeError("Pipeline already running for this race")
        pipe = ProcessingPipeline(race_id, session_factory)
        _pipelines[race_id] = pipe
    pipe.start(input_type, input_path, options)
    return pipe


def stop_pipeline(race_id: str) -> None:
    pipe = _pipelines.get(race_id)
    if pipe:
        pipe.stop()
