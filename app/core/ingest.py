"""Frame source abstraction – MP4 file & RTMP stream via FFmpeg."""

from __future__ import annotations

import abc
import logging
import subprocess
import time
from typing import Generator

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class Frame:
    """Container for a single video frame with timestamp."""

    __slots__ = ("image", "timestamp_ms", "index")

    def __init__(self, image: np.ndarray, timestamp_ms: float, index: int):
        self.image = image
        self.timestamp_ms = timestamp_ms
        self.index = index


class FrameSource(abc.ABC):
    """Abstract interface for frame producers."""

    @abc.abstractmethod
    def frames(self) -> Generator[Frame, None, None]:
        """Yield Frame objects sequentially."""

    @abc.abstractmethod
    def release(self) -> None:
        """Clean up resources."""

    @property
    @abc.abstractmethod
    def width(self) -> int: ...

    @property
    @abc.abstractmethod
    def height(self) -> int: ...

    @property
    @abc.abstractmethod
    def fps(self) -> float: ...


# ── MP4 file source ────────────────────────────────────────────────

class FileSource(FrameSource):
    """Read frames from a local video file using OpenCV."""

    def __init__(self, path: str):
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video file: {path}")
        self._w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        logger.info("FileSource opened %s  %dx%d @ %.1f fps", path, self._w, self._h, self._fps)

    @property
    def width(self) -> int:
        return self._w

    @property
    def height(self) -> int:
        return self._h

    @property
    def fps(self) -> float:
        return self._fps

    def frames(self) -> Generator[Frame, None, None]:
        idx = 0
        while self._cap.isOpened():
            ok, img = self._cap.read()
            if not ok:
                break
            ts = self._cap.get(cv2.CAP_PROP_POS_MSEC)
            yield Frame(image=img, timestamp_ms=ts, index=idx)
            idx += 1

    def release(self) -> None:
        self._cap.release()


# ── RTMP stream source via FFmpeg ───────────────────────────────────

class RtmpSource(FrameSource):
    """Ingest an RTMP stream by piping raw frames from FFmpeg."""

    def __init__(self, url: str, width: int = 1920, height: int = 1080, fps: float = 30.0):
        self._url = url
        self._w = width
        self._h = height
        self._fps = fps
        self._proc: subprocess.Popen | None = None

    @property
    def width(self) -> int:
        return self._w

    @property
    def height(self) -> int:
        return self._h

    @property
    def fps(self) -> float:
        return self._fps

    def _start(self) -> subprocess.Popen:
        cmd = [
            "ffmpeg",
            "-loglevel", "warning",
            "-i", self._url,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-r", str(self._fps),
            "-s", f"{self._w}x{self._h}",
            "-",
        ]
        logger.info("Starting FFmpeg: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc

    def frames(self) -> Generator[Frame, None, None]:
        self._proc = self._start()
        assert self._proc.stdout is not None
        frame_size = self._w * self._h * 3
        idx = 0
        t0 = time.monotonic()
        while True:
            raw = self._proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break
            img = np.frombuffer(raw, dtype=np.uint8).reshape((self._h, self._w, 3))
            ts_ms = (time.monotonic() - t0) * 1000.0
            yield Frame(image=img, timestamp_ms=ts_ms, index=idx)
            idx += 1

    def release(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None


def create_source(input_type: str, input_path: str, **kwargs) -> FrameSource:
    """Factory to create the appropriate FrameSource."""
    if input_type == "file":
        return FileSource(input_path)
    elif input_type == "rtmp":
        return RtmpSource(input_path, **kwargs)
    else:
        raise ValueError(f"Unsupported input_type: {input_type}")
