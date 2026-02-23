"""Burst capture – ring buffer of recent frames + evidence persistence."""

from __future__ import annotations

import logging
import os
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from app.core.ingest import Frame
from app.core.crossing import FinishEvent

logger = logging.getLogger(__name__)


class BurstBuffer:
    """Maintains a sliding window of recent frames so we can extract
    N_BEFORE + 1 + N_AFTER frames around a finish event.
    """

    def __init__(self, frames_before: int = 10, frames_after: int = 10):
        self._before = frames_before
        self._after = frames_after
        # Store (frame_copy, timestamp_ms, index) – we copy to avoid mutation
        self._buffer: deque[Frame] = deque(maxlen=frames_before + 1)
        # Pending events waiting for N_AFTER frames
        self._pending: list[tuple[FinishEvent, list[Frame]]] = []

    @property
    def frames_needed_after(self) -> int:
        return self._after

    def push(self, frame: Frame) -> list[tuple[FinishEvent, list[Frame]]]:
        """Push a new frame; returns completed bursts (if any)."""
        # Store a copy
        stored = Frame(image=frame.image.copy(), timestamp_ms=frame.timestamp_ms, index=frame.index)
        self._buffer.append(stored)

        # Collect after-frames for pending events
        completed: list[tuple[FinishEvent, list[Frame]]] = []
        still_pending = []
        for ev, frames in self._pending:
            frames.append(stored)
            needed = self._before + 1 + self._after
            if len(frames) >= needed:
                completed.append((ev, frames))
            else:
                still_pending.append((ev, frames))
        self._pending = still_pending

        return completed

    def trigger(self, event: FinishEvent) -> None:
        """Register a finish event – start collecting its burst frames."""
        # Gather frames already in buffer (the 'before' portion + crossing frame)
        pre_frames = list(self._buffer)  # shallow copy of deque contents
        self._pending.append((event, pre_frames))

    def flush_all(self) -> list[tuple[FinishEvent, list[Frame]]]:
        """Return all pending bursts even if they don't have enough after-frames."""
        out = list(self._pending)
        self._pending.clear()
        return out


def save_burst(
    event: FinishEvent,
    burst_frames: list[Frame],
    base_dir: str,
    race_id: str,
) -> tuple[str, str]:
    """Persist burst frames and crossing thumbnail to disk.

    Returns (crossing_frame_path, burst_dir).
    """
    burst_dir = os.path.join(base_dir, race_id, f"track_{event.track_id}")
    Path(burst_dir).mkdir(parents=True, exist_ok=True)

    crossing_path = ""
    for i, f in enumerate(burst_frames):
        fname = f"frame_{i:04d}_ts{int(f.timestamp_ms)}.jpg"
        fpath = os.path.join(burst_dir, fname)
        cv2.imwrite(fpath, f.image)
        if f.index == event.frame_index:
            crossing_path = fpath

    # If exact crossing frame not in burst, save from event detection
    if not crossing_path:
        crossing_path = os.path.join(burst_dir, "crossing.jpg")
        # We don't have the raw frame stored on the event, so pick the closest
        if burst_frames:
            closest = min(burst_frames, key=lambda fr: abs(fr.index - event.frame_index))
            cv2.imwrite(crossing_path, closest.image)

    logger.info("Burst saved  track=%d  dir=%s  frames=%d", event.track_id, burst_dir, len(burst_frames))
    return crossing_path, burst_dir
