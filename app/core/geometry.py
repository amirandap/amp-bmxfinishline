"""Geometry helpers – polygon containment, side-of-line, crossing detection."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike


# ── Point-in-polygon (ray casting) ─────────────────────────────────

def point_in_polygon(px: float, py: float, polygon: list[list[float]]) -> bool:
    """Ray-casting algorithm.  *polygon* is [[x,y], …], closed automatically."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ── Side of a directed line ────────────────────────────────────────

def side_of_line(
    px: float,
    py: float,
    line: list[list[float]],
) -> float:
    """Return the cross-product sign for point (px,py) relative to the
    directed line from line[0] to line[1].

    Positive  → left side
    Negative  → right side
    Zero      → exactly on the line
    """
    (x1, y1), (x2, y2) = line
    return (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)


def has_crossed(
    prev_side: float,
    curr_side: float,
) -> bool:
    """True when the sign changes (excluding zero → non-zero)."""
    if prev_side == 0.0 or curr_side == 0.0:
        return False
    return (prev_side > 0) != (curr_side > 0)


# ── Polygon mask for OpenCV frame ──────────────────────────────────

def polygon_mask(frame_shape: tuple[int, ...], polygon: list[list[float]]) -> np.ndarray:
    """Return a uint8 mask (255 inside, 0 outside) for a polygon on a frame."""
    import cv2

    mask = np.zeros(frame_shape[:2], dtype=np.uint8)
    pts = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
    cv2.fillPoly(mask, [pts], 255)
    return mask


# ── Utilities ───────────────────────────────────────────────────────

def bbox_bottom_center(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float]:
    """Return bottom-center of a bounding box."""
    return ((x1 + x2) / 2.0, y2)
