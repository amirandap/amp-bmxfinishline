"""YOLO detection + ByteTrack tracking inside ROI."""

from __future__ import annotations

import logging

import cv2
import numpy as np
from ultralytics import YOLO

from app.config import AppConfig
from app.core.geometry import point_in_polygon, polygon_mask
from app.core.types import Detection

logger = logging.getLogger(__name__)


class Detector:
    """YOLO detector + tracker wrapper."""

    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        device = cfg.yolo.device or None
        self._model = YOLO(cfg.yolo.model)
        if device:
            self._model.to(device)
        self._tracker_cfg = self._build_tracker_yaml(cfg)
        logger.info(
            "Detector ready  model=%s  tracker=%s  device=%s",
            cfg.yolo.model, cfg.tracker.type, device or "auto",
        )

    # Ultralytics accepts a YAML file or built-in name for tracker config.
    # We write a temp YAML so we can customise params.
    @staticmethod
    def _build_tracker_yaml(cfg: AppConfig) -> str:
        import tempfile, yaml

        tracker_dict = {
            "tracker_type": cfg.tracker.type,
            "track_high_thresh": cfg.tracker.track_high_thresh,
            "track_low_thresh": cfg.tracker.track_low_thresh,
            "new_track_thresh": cfg.tracker.new_track_thresh,
            "track_buffer": cfg.tracker.track_buffer,
            "match_thresh": cfg.tracker.match_thresh,
        }
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(tracker_dict, tmp)
        tmp.flush()
        return tmp.name

    def detect_and_track(
        self,
        frame: np.ndarray,
        roi_polygon: list[list[float]] | None = None,
    ) -> list[Detection]:
        """Run detection + tracking on *frame*, filtering to ROI."""
        # Optionally mask frame outside ROI to reduce false positives
        input_frame = frame
        if roi_polygon:
            mask = polygon_mask(frame.shape, roi_polygon)
            input_frame = cv2.bitwise_and(frame, frame, mask=mask)

        results = self._model.track(
            input_frame,
            persist=True,
            conf=self._cfg.yolo.confidence,
            iou=self._cfg.yolo.iou,
            classes=self._cfg.yolo.classes,
            tracker=self._tracker_cfg,
            verbose=False,
        )

        detections: list[Detection] = []
        if not results or results[0].boxes is None:
            return detections

        boxes = results[0].boxes
        for box in boxes:
            # box fields: xyxy, id, conf, cls
            if box.id is None:
                continue
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            tid = int(box.id[0].item())
            conf = float(box.conf[0].item())
            cls = int(box.cls[0].item())
            det = Detection(
                track_id=tid, x1=x1, y1=y1, x2=x2, y2=y2,
                confidence=conf, class_id=cls,
            )
            # Filter: bottom-center must be inside ROI
            if roi_polygon and not point_in_polygon(*det.bottom_center, roi_polygon):
                continue
            detections.append(det)

        return detections
