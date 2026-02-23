"""Finish-line crossing detection logic."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from app.core.geometry import side_of_line, has_crossed
from app.core.types import Detection

logger = logging.getLogger(__name__)


@dataclass
class FinishEvent:
    """Represents one rider crossing the finish line."""
    track_id: int
    timestamp_ms: float       # sub-frame interpolated when enabled
    frame_index: int
    detection: Detection
    interpolated: bool = False  # True when timestamp was sub-frame refined


def _direction_matches(prev: float, curr: float, direction: str) -> bool:
    """Check whether the sign change satisfies the required crossing direction.

    pos_to_neg – rider was on the positive (left) side and moved to negative (right)
    neg_to_pos – rider was on the negative (right) side and moved to positive (left)
    any        – either direction accepted
    """
    if direction == "pos_to_neg":
        return prev > 0 and curr < 0
    if direction == "neg_to_pos":
        return prev < 0 and curr > 0
    return True  # "any"


class CrossingDetector:
    """Tracks which side of the finish line each track_id is on and fires
    events when a crossing is detected.

    Enforces:
      - each track_id can finish at most once
      - configurable cooldown between any two finish events
      - optional direction filter (pos_to_neg | neg_to_pos | any)
      - configurable reference point on the detection box
      - sub-frame timestamp interpolation for higher timing precision
    """

    def __init__(
        self,
        finish_line: list[list[float]],
        cooldown_seconds: float = 2.0,
        crossing_direction: str = "any",
        crossing_point: str = "bottom_center",
        interpolate_timestamp: bool = True,
    ):
        self._finish_line = finish_line
        self._cooldown_ms = cooldown_seconds * 1000.0
        self._direction = crossing_direction
        self._crossing_point = crossing_point
        self._interpolate = interpolate_timestamp
        # track_id → (side_value, timestamp_ms)
        self._prev: dict[int, tuple[float, float]] = {}
        # track_id → finished flag
        self._finished: set[int] = set()
        # timestamp of last global finish event
        self._last_event_ts: float = -1e9

    def _ref_point(self, det: Detection) -> tuple[float, float]:
        return det.crossing_point(self._crossing_point)

    def update(
        self,
        detections: list[Detection],
        timestamp_ms: float,
        frame_index: int,
    ) -> list[FinishEvent]:
        """Feed detections for one frame; return any new FinishEvents."""
        events: list[FinishEvent] = []
        for det in detections:
            tid = det.track_id
            if tid in self._finished:
                continue

            curr_side = side_of_line(*self._ref_point(det), self._finish_line)
            prev_entry = self._prev.get(tid)
            self._prev[tid] = (curr_side, timestamp_ms)

            if prev_entry is None:
                continue

            prev_side, prev_ts = prev_entry

            if not has_crossed(prev_side, curr_side):
                continue

            if not _direction_matches(prev_side, curr_side, self._direction):
                logger.debug(
                    "Crossing suppressed by direction filter  track=%d  direction=%s  "
                    "prev_side=%.3f  curr_side=%.3f",
                    tid, self._direction, prev_side, curr_side,
                )
                # Do NOT mark as finished – the rider may cross again in the
                # correct direction (e.g. practice run in wrong direction).
                continue

            # Cooldown check
            if (timestamp_ms - self._last_event_ts) < self._cooldown_ms:
                logger.debug("Crossing suppressed by cooldown  track=%d  ts=%.1f", tid, timestamp_ms)
                continue

            # ── Sub-frame timestamp interpolation ─────────────────────────
            # Linear interpolation: find the fraction of the frame interval
            # at which abs(side) == 0, weighted by the magnitudes of both sides.
            #
            #   t_cross = t_prev + (t_curr - t_prev) × |prev| / (|prev| + |curr|)
            #
            # This reduces timing error from ±(1 frame) to near-zero for
            # uniform-speed motion, matching the standard sports-timing approach.
            interpolated = False
            refined_ts = timestamp_ms
            if self._interpolate and prev_side != 0.0 and curr_side != 0.0:
                abs_prev = abs(prev_side)
                abs_curr = abs(curr_side)
                alpha = abs_prev / (abs_prev + abs_curr)  # fraction along frame interval
                refined_ts = prev_ts + alpha * (timestamp_ms - prev_ts)
                interpolated = True
                logger.debug(
                    "Sub-frame timestamp  track=%d  prev_ts=%.3f  curr_ts=%.3f  "
                    "alpha=%.4f  refined=%.3f",
                    tid, prev_ts, timestamp_ms, alpha, refined_ts,
                )

            self._finished.add(tid)
            self._last_event_ts = timestamp_ms
            ev = FinishEvent(
                track_id=tid,
                timestamp_ms=refined_ts,
                frame_index=frame_index,
                detection=det,
                interpolated=interpolated,
            )
            events.append(ev)
            logger.info(
                "FINISH  track=%d  ts=%.3f ms  interpolated=%s  pos=(%.0f,%.0f)",
                tid, refined_ts, interpolated, *self._ref_point(det),
            )
        return events

    def reset(self) -> None:
        self._prev.clear()
        self._finished.clear()
        self._last_event_ts = -1e9
