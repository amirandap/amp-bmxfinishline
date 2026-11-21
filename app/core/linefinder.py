"""Automatic finish-line detection using OpenCV LSD (with HoughLinesP fallback).

Algorithm
---------
1. Optionally crop to a fixed ROI (default: full width × bottom 80 % of the frame).
2. Apply CLAHE contrast enhancement (handles chalk/paint lines on low-contrast
   backgrounds such as dirt or asphalt).
3. Convert to greyscale + Gaussian blur.
4. Run LSD; if the OpenCV build does not include it, fall back to
   Canny → HoughLinesP.
5. Pre-filter segments by angle and y-band (no length filter yet), then
   merge near-collinear fragments into longer composite segments.
6. Apply length filter and score every surviving segment:
   - angle from horizontal must be < *angle_thresh_deg* (default 25 °)
   - length must be ≥ *min_length_frac* × crop-width (default 10 %)
   - mid-point must lie within *expected_y_frac* vertical band of the crop
7. If the segment approach finds nothing, fall back to a horizontal
   edge-density scan: the row with the most Canny edge pixels is returned
   as a full-width finish line — robust for broken chalk on dirt / asphalt.

For side-on cameras where the finish line appears **vertical** in the frame,
pass ``orientation='vertical'``; the same scoring logic applies but rotated 90 °.
"""

from __future__ import annotations

import logging
import math
from typing import Literal

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ── Frame extraction ────────────────────────────────────────────────

def extract_frame(video_path: str, frame_no: int = 0) -> np.ndarray | None:
    """Read a single frame from *video_path*. Returns ``None`` on failure."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("Cannot open video: %s", video_path)
        return None
    if frame_no > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        logger.error("Could not read frame %d from %s", frame_no, video_path)
    return frame if ok else None


# ── Segment scoring ─────────────────────────────────────────────────

def _angle_from_axis(dx: float, dy: float, orientation: str) -> float:
    """Return degrees from the target axis (0 = perfectly aligned)."""
    if orientation == "vertical":
        # For vertical lines: dx ≈ 0, dy >> 0 → angle from vertical
        return abs(math.degrees(math.atan2(abs(dx), abs(dy) + 1e-9)))
    # horizontal: dy ≈ 0, dx >> 0
    return abs(math.degrees(math.atan2(abs(dy), abs(dx) + 1e-9)))


def _score_segment(
    x1: float, y1: float, x2: float, y2: float,
    crop_w: int, crop_h: int,
    orientation: str,
    angle_thresh_deg: float,
    min_length_frac: float,
    expected_y_frac: tuple[float, float],
) -> float | None:
    """Score one segment; return ``None`` if any hard filter fails."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)

    # Hard filter: minimum length
    min_len = min_length_frac * (crop_h if orientation == "vertical" else crop_w)
    if length < max(min_len, 10.0):
        return None

    # Hard filter: angle from target axis
    angle_deg = _angle_from_axis(dx, dy, orientation)
    if angle_deg > angle_thresh_deg:
        return None

    # Hard filter: mid-point y-fraction inside expected band
    mid_y = (y1 + y2) / 2.0
    yf = mid_y / (crop_h or 1)
    y_lo, y_hi = expected_y_frac
    if not (y_lo <= yf <= y_hi):
        return None

    # Continuous score (higher = better finish-line candidate)
    len_score   = length / (crop_w if orientation == "horizontal" else crop_h)
    align_score = 1.0 - angle_deg / (angle_thresh_deg + 1e-9)
    band_centre = (y_lo + y_hi) / 2.0
    band_half   = (y_hi - y_lo) / 2.0 + 1e-9
    y_center    = 1.0 - abs(yf - band_centre) / band_half

    return 0.6 * len_score + 0.3 * align_score + 0.1 * y_center


# ── Line segment detection ──────────────────────────────────────────

def _run_lsd(gray: np.ndarray) -> np.ndarray | None:
    """Try OpenCV LSD; return N×1×4 array or None if unavailable."""
    try:
        lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
        lines, *_ = lsd.detect(gray)
        return lines  # shape (N, 1, 4) or None
    except (cv2.error, AttributeError):
        return None


def _run_hough(gray: np.ndarray, min_length_px: int) -> np.ndarray | None:
    """Canny + HoughLinesP fallback; return N×1×4 float32 or None."""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    raw = cv2.HoughLinesP(
        edges, 1, np.pi / 180,
        threshold=40,
        minLineLength=min_length_px,
        maxLineGap=20,
    )
    if raw is None:
        return None
    # raw: (N, 1, 4) int32 → float32 for uniform handling
    return raw.astype(np.float32)


def _edge_density_line(
    gray: np.ndarray,
    expected_y_frac: tuple[float, float],
    orientation: str = "horizontal",
    min_density_frac: float = 0.04,
) -> tuple[float, float, float, float] | None:
    """Find the row/column with the highest Canny edge density in the expected band.

    This is a robust fallback for chalk/paint lines on low-contrast backgrounds
    where LSD and Hough find only short fragments.  Returns a full-width segment
    at the peak row, or ``None`` if the peak edge density is too low.
    """
    h, w = gray.shape
    edges = cv2.Canny(gray, 30, 90)

    if orientation == "horizontal":
        lo = max(0, int(expected_y_frac[0] * h))
        hi = min(h - 1, int(expected_y_frac[1] * h))
        band = edges[lo : hi + 1]          # shape (band_h, w)
        row_scores = band.sum(axis=1) / 255.0
        if len(row_scores) == 0:
            return None
        # Smooth to avoid noisy single-pixel peaks
        kernel = np.ones(max(3, h // 50)) / max(3, h // 50)
        smoothed = np.convolve(row_scores, kernel, mode="same")
        best_idx = int(np.argmax(smoothed))
        if smoothed[best_idx] < w * min_density_frac:
            return None
        best_y = float(lo + best_idx)
        return (0.0, best_y, float(w), best_y)
    else:  # vertical
        lo = max(0, int(expected_y_frac[0] * w))
        hi = min(w - 1, int(expected_y_frac[1] * w))
        band = edges[:, lo : hi + 1]       # shape (h, band_w)
        col_scores = band.sum(axis=0) / 255.0
        if len(col_scores) == 0:
            return None
        kernel = np.ones(max(3, w // 50)) / max(3, w // 50)
        smoothed = np.convolve(col_scores, kernel, mode="same")
        best_idx = int(np.argmax(smoothed))
        if smoothed[best_idx] < h * min_density_frac:
            return None
        best_x = float(lo + best_idx)
        return (best_x, 0.0, best_x, float(h))


# ── Merge near-collinear candidates ────────────────────────────────

def _merge_collinear(
    segments: list[tuple[float, float, float, float]],
    angle_tol: float = 5.0,
    dist_tol: float = 20.0,
) -> list[tuple[float, float, float, float]]:
    """Merge segments whose angles and positions are very close.

    Simple greedy approach: sort by score descending, merge the rest into
    the highest-scoring one if they are nearly co-linear.
    """
    if len(segments) <= 1:
        return segments

    merged: list[tuple[float, float, float, float]] = []
    used = [False] * len(segments)

    for i, seg_i in enumerate(segments):
        if used[i]:
            continue
        x1i, y1i, x2i, y2i = seg_i
        dx_i, dy_i = x2i - x1i, y2i - y1i
        angle_i = math.degrees(math.atan2(dy_i, dx_i + 1e-9))
        group = [seg_i]
        for j, seg_j in enumerate(segments):
            if i == j or used[j]:
                continue
            x1j, y1j, x2j, y2j = seg_j
            dx_j, dy_j = x2j - x1j, y2j - y1j
            angle_j = math.degrees(math.atan2(dy_j, dx_j + 1e-9))
            if abs(angle_i - angle_j) > angle_tol:
                continue
            # Perpendicular distance from segment-j midpoint to segment-i line
            mid_jx, mid_jy = (x1j + x2j) / 2, (y1j + y2j) / 2
            li = math.hypot(dx_i, dy_i) + 1e-9
            d = abs(dy_i * mid_jx - dx_i * mid_jy + x2i * y1i - y2i * x1i) / li
            if d < dist_tol:
                group.append(seg_j)
                used[j] = True
        # Build merged segment spanning the extremes of the group
        all_x = [p[0] for p in group] + [p[2] for p in group]
        all_y = [p[1] for p in group] + [p[3] for p in group]
        # Project onto the dominant axis of seg_i
        ux, uy = dx_i / (math.hypot(dx_i, dy_i) + 1e-9), dy_i / (math.hypot(dx_i, dy_i) + 1e-9)
        projs = [ux * x + uy * y for x, y in zip(all_x, all_y)]
        t_min, t_max = min(projs), max(projs)
        ox, oy = x1i - ux * (ux * x1i + uy * y1i), y1i - uy * (ux * x1i + uy * y1i)
        merged.append((ox + ux * t_min, oy + uy * t_min, ox + ux * t_max, oy + uy * t_max))
        used[i] = True

    return merged


# ── Public API ──────────────────────────────────────────────────────

def detect_finish_line(
    frame: np.ndarray,
    *,
    roi_xywh: tuple[int, int, int, int] | None = None,
    orientation: Literal["horizontal", "vertical"] = "horizontal",
    angle_thresh_deg: float = 25.0,
    min_length_frac: float = 0.10,
    expected_y_frac: tuple[float, float] = (0.2, 0.9),
    merge_collinear: bool = True,
) -> tuple[list[float], list[float]] | None:
    """Detect the most likely finish-line segment in *frame*.

    Parameters
    ----------
    frame           BGR numpy array.
    roi_xywh        Crop region *(x, y, w, h)* in frame pixels.
                    When ``None`` the full width × bottom 80 % of the frame
                    is used as the default ROI.
    orientation     ``'horizontal'`` (finish bar across frame) or
                    ``'vertical'`` (side-on finish line running top→bottom).
    angle_thresh_deg  Maximum deviation from the target axis (degrees).
    min_length_frac   Minimum segment length as fraction of crop width
                    (applied **after** collinear merging).
    expected_y_frac   (lo, hi) vertical band within the crop (0–1).
    merge_collinear   Whether to merge near-collinear segments before scoring.

    Algorithm
    ---------
    1. ROI crop (default: full width, bottom 80 %).
    2. CLAHE contrast enhancement + Gaussian blur.
    3. LSD (or Canny+HoughLinesP fallback) to collect raw segments.
    4. Pre-filter by angle and y-band only (NO length filter yet).
    5. Merge collinear fragments into longer segments.
    6. Apply length filter and score; return best candidate.
    7. If segment approach produces no candidates, fall back to a
       horizontal edge-density scan to locate the finish line row.

    Returns
    -------
    ``([x1, y1], [x2, y2])`` in **original** frame coordinates, or ``None``.
    """
    h, w = frame.shape[:2]

    # Default ROI: full width, bottom 80 % (finish lines are rarely at the top)
    if roi_xywh is None:
        rx = 0
        ry = int(h * 0.20)
        rw = w
        rh = h - ry
        roi_xywh = (rx, ry, rw, rh)

    rx, ry, rw, rh = [int(v) for v in roi_xywh]
    rx = max(0, min(rx, w - 1))
    ry = max(0, min(ry, h - 1))
    rw = max(1, min(rw, w - rx))
    rh = max(1, min(rh, h - ry))
    ox, oy = rx, ry  # offset to restore original coordinates

    crop = frame[ry : ry + rh, rx : rx + rw]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # CLAHE: enhance low-contrast chalk/paint lines on dirt or asphalt
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    # Very short minimum for pre-merge collection (3 % of crop width)
    premix_min_px = max(10, int(0.03 * (rh if orientation == "vertical" else rw)))

    lines = _run_lsd(gray)
    if lines is None or len(lines) == 0:
        logger.debug("LSD unavailable or returned nothing — using HoughLinesP")
        lines = _run_hough(gray, premix_min_px)

    candidates: list[tuple[float, tuple[float, float, float, float]]] = []

    if lines is not None and len(lines) > 0:
        # Phase 1: pre-filter by angle + y-band only (no length filter yet)
        # This lets short collinear fragments survive into the merge step.
        pre_merge: list[tuple[float, float, float, float]] = []
        for line in lines:
            seg = line[0]
            x1, y1, x2, y2 = float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])
            dx, dy = x2 - x1, y2 - y1
            if _angle_from_axis(dx, dy, orientation) > angle_thresh_deg:
                continue
            mid_y = (y1 + y2) / 2.0
            yf = mid_y / (rh or 1)
            y_lo, y_hi = expected_y_frac
            if not (y_lo <= yf <= y_hi):
                continue
            pre_merge.append((x1, y1, x2, y2))

        if pre_merge:
            # Phase 2: merge collinear fragments
            merged = _merge_collinear(pre_merge, angle_tol=5.0, dist_tol=30.0) if merge_collinear and len(pre_merge) > 1 else pre_merge

            # Phase 3: score merged segments (length filter applied here)
            for x1, y1, x2, y2 in merged:
                score = _score_segment(
                    x1, y1, x2, y2, rw, rh,
                    orientation, angle_thresh_deg, min_length_frac, expected_y_frac,
                )
                if score is not None:
                    candidates.append((score, (x1, y1, x2, y2)))

    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        bx1, by1, bx2, by2 = candidates[0][1]
        fx1, fy1 = bx1 + ox, by1 + oy
        fx2, fy2 = bx2 + ox, by2 + oy
        logger.info(
            "Auto-detected finish line (segment): (%.0f,%.0f)→(%.0f,%.0f)  score=%.3f",
            fx1, fy1, fx2, fy2, candidates[0][0],
        )
        return ([fx1, fy1], [fx2, fy2])

    # Fallback: horizontal edge-density scan — robust for chalk lines on dirt
    logger.debug("Segment approach found no candidates; trying edge-density fallback")
    density_seg = _edge_density_line(gray, expected_y_frac, orientation)
    if density_seg is not None:
        bx1, by1, bx2, by2 = density_seg
        fx1, fy1 = bx1 + ox, by1 + oy
        fx2, fy2 = bx2 + ox, by2 + oy
        logger.info(
            "Auto-detected finish line (edge density): (%.0f,%.0f)→(%.0f,%.0f)",
            fx1, fy1, fx2, fy2,
        )
        return ([fx1, fy1], [fx2, fy2])

    logger.warning(
        "Could not detect a finish line (orientation=%s, angle_thresh=%.0f°)",
        orientation, angle_thresh_deg,
    )
    return None


def auto_calibrate_from_video(
    video_path: str,
    frame_no: int = 0,
    roi_xywh: tuple[int, int, int, int] | None = None,
    orientation: Literal["horizontal", "vertical"] = "horizontal",
    angle_thresh_deg: float = 25.0,
    expected_y_frac: tuple[float, float] = (0.2, 0.9),
) -> dict | None:
    """Extract one frame from *video_path* and run finish-line detection.

    Returns a partial calibration dict ``{"finish_line": [...], "frame_size": [w, h]}``
    or ``None`` when detection fails.
    """
    frame = extract_frame(video_path, frame_no)
    if frame is None:
        return None

    h, w = frame.shape[:2]
    result = detect_finish_line(
        frame,
        roi_xywh=roi_xywh,
        orientation=orientation,
        angle_thresh_deg=angle_thresh_deg,
        expected_y_frac=expected_y_frac,
    )
    if result is None:
        return None

    pt1, pt2 = result
    return {
        "finish_line": [pt1, pt2],
        "frame_size": [w, h],
    }
