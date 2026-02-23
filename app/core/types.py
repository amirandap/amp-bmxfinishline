"""Shared data types used across core modules."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.geometry import bbox_bottom_center


@dataclass
class Detection:
    """A single detected/tracked object in a frame."""
    track_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int
    bottom_center: tuple[float, float] = field(init=False)

    def __post_init__(self):
        self.bottom_center = bbox_bottom_center(self.x1, self.y1, self.x2, self.y2)

    def crossing_point(self, mode: str) -> tuple[float, float]:
        """Return the configured reference point used for crossing detection.

        Modes
        -----
        bottom_center  – mid-point of the bottom edge (foot / wheel contact)
        center         – centre of the bounding box
        top_center     – mid-point of the top edge
        front_left     – bottom-left corner  (for riders travelling right→left)
        front_right    – bottom-right corner (for riders travelling left→right)
        """
        cx = (self.x1 + self.x2) / 2.0
        cy = (self.y1 + self.y2) / 2.0
        if mode == "center":
            return cx, cy
        if mode == "top_center":
            return cx, self.y1
        if mode == "front_left":
            return self.x1, self.y2
        if mode == "front_right":
            return self.x2, self.y2
        # default: bottom_center
        return self.bottom_center
