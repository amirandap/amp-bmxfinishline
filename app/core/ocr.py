"""OCR bib extraction and confidence-weighted fusion."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import cv2
import numpy as np

from app.config import OcrConfig
from app.core.ingest import Frame
from app.core.types import Detection

logger = logging.getLogger(__name__)


@dataclass
class OcrCandidate:
    bib: str
    score: float


@dataclass
class OcrResult:
    bib: str | None
    confidence: float
    candidates: list[dict]  # [{bib: str, score: float}, ...]


# ── OCR engine abstraction ──────────────────────────────────────────

class _OcrEngine:
    """Thin wrapper around PaddleOCR / EasyOCR."""

    def __init__(self, engine: str):
        self._engine_name = engine
        self._engine = None

    def _lazy_init(self):
        if self._engine is not None:
            return
        if self._engine_name == "paddleocr":
            try:
                from paddleocr import PaddleOCR
                self._engine = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
            except ImportError:
                logger.warning("paddleocr not installed; falling back to easyocr")
                self._engine_name = "easyocr"
                self._lazy_init()
        elif self._engine_name == "easyocr":
            try:
                import easyocr
                self._engine = easyocr.Reader(["en"], gpu=False, verbose=False)
            except ImportError:
                logger.warning("easyocr not installed; falling back to tesseract")
                self._engine_name = "tesseract"
                self._lazy_init()
        elif self._engine_name == "tesseract":
            try:
                import pytesseract  # noqa: F401 – just verify it's importable
                self._engine = "tesseract"
            except ImportError:
                raise RuntimeError(
                    "No OCR engine available. "
                    "Install one of: paddleocr, easyocr, or pytesseract."
                )
        else:
            raise ValueError(f"Unknown OCR engine: {self._engine_name}")

    def read(self, image: np.ndarray) -> list[tuple[str, float]]:
        """Return list of (text, confidence) from the image."""
        self._lazy_init()
        results: list[tuple[str, float]] = []
        if self._engine_name == "paddleocr":
            out = self._engine.ocr(image, cls=False)
            if out and out[0]:
                for line in out[0]:
                    text = line[1][0]
                    conf = float(line[1][1])
                    results.append((text, conf))
        elif self._engine_name == "easyocr":
            out = self._engine.readtext(image)
            for (_, text, conf) in out:
                results.append((text, float(conf)))
        elif self._engine_name == "tesseract":
            import pytesseract
            # PSM 7 = single line; PSM 6 = single block
            for psm in (7, 6):
                cfg = f"--oem 1 --psm {psm} -c tessedit_char_whitelist=0123456789"
                text = pytesseract.image_to_string(image, config=cfg).strip()
                data = pytesseract.image_to_data(
                    image, config=cfg, output_type=pytesseract.Output.DICT
                )
                for t, c in zip(data["text"], data["conf"]):
                    t = t.strip()
                    if t and int(c) > 0:
                        results.append((t, float(c) / 100.0))
        return results


_ocr_engine: _OcrEngine | None = None


def get_ocr_engine(engine: str = "paddleocr") -> _OcrEngine:
    global _ocr_engine
    if _ocr_engine is None or _ocr_engine._engine_name != engine:
        _ocr_engine = _OcrEngine(engine)
    return _ocr_engine


# ── Crop helpers ────────────────────────────────────────────────────

def _crop_reading_zone(
    frame: np.ndarray,
    reading_zone: list[list[float]] | None,
    detection: Detection,
    zoom_scale: float,
) -> np.ndarray:
    """Crop the bib region from a frame."""
    h, w = frame.shape[:2]

    if reading_zone and len(reading_zone) >= 3:
        # Use reading zone polygon bounding rect
        pts = np.array(reading_zone, dtype=np.int32)
        rx, ry, rw, rh = cv2.boundingRect(pts)
        crop = frame[max(0, ry):min(h, ry + rh), max(0, rx):min(w, rx + rw)]
    else:
        # Heuristic: front/lower-middle of the rider bbox
        bx1, by1, bx2, by2 = int(detection.x1), int(detection.y1), int(detection.x2), int(detection.y2)
        bw = bx2 - bx1
        bh = by2 - by1
        # Bib is typically on the upper-middle torso area
        # Use the middle horizontal third, upper 40% of bbox
        cx1 = bx1 + bw // 4
        cx2 = bx2 - bw // 4
        cy1 = by1 + int(bh * 0.15)
        cy2 = by1 + int(bh * 0.55)
        cx1, cy1 = max(0, cx1), max(0, cy1)
        cx2, cy2 = min(w, cx2), min(h, cy2)
        crop = frame[cy1:cy2, cx1:cx2]

    if crop.size == 0:
        return crop

    # Digital zoom
    if zoom_scale > 1.0:
        new_w = int(crop.shape[1] * zoom_scale)
        new_h = int(crop.shape[0] * zoom_scale)
        crop = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

    return crop


_DIGIT_SUBS: dict[str, str] = {
    "S": "5", "s": "5",
    "O": "0", "o": "0",
    "I": "1", "l": "1",
    "Z": "2",
    "B": "8",
    "G": "6",
    "g": "9", "q": "9",
}


def _normalize_ocr(text: str) -> str:
    """Replace common OCR letter-for-digit misreads before digit filtering."""
    return "".join(_DIGIT_SUBS.get(c, c) for c in text)


def _filter_digits(text: str, max_digits: int) -> str | None:
    """Extract 1-max_digits digit string from OCR text, after normalizing lookalikes."""
    normalized = _normalize_ocr(text)
    digits = re.sub(r"[^0-9]", "", normalized)
    if 1 <= len(digits) <= max_digits:
        return digits
    if len(digits) > max_digits:
        return digits[:max_digits]
    return None


# ── Fusion: confidence-weighted voting ──────────────────────────────

def fuse_readings(readings: list[tuple[str, float]], top_n: int = 3) -> OcrResult:
    """Fuse multiple OCR readings into a final bib via weighted voting.

    *readings* is [(bib_string, confidence), ...].
    """
    if not readings:
        return OcrResult(bib=None, confidence=0.0, candidates=[])

    scores: dict[str, float] = {}
    counts: dict[str, int] = {}
    for bib, conf in readings:
        scores[bib] = scores.get(bib, 0.0) + conf
        counts[bib] = counts.get(bib, 0) + 1

    # Normalise by number of readings to get average confidence
    avg_scores = {bib: scores[bib] / counts[bib] for bib in scores}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    candidates = [{"bib": b, "score": round(s, 4)} for b, s in ranked[:top_n]]

    best_bib, best_total = ranked[0]
    best_conf = avg_scores[best_bib]

    return OcrResult(bib=best_bib, confidence=round(best_conf, 4), candidates=candidates)


# ── Main entry point ────────────────────────────────────────────────

def extract_bib(
    burst_frames: list[Frame],
    detection: Detection,
    cfg: OcrConfig,
    reading_zone: list[list[float]] | None = None,
    crossing_frame_index: int | None = None,
) -> OcrResult:
    """Run OCR on burst frames at multiple scales and return fused bib result.

    Frames are processed in approach-first order (pre-crossing frames before
    post-crossing frames) because the rider's bib faces the camera during
    approach but is hidden after passing.  If a high-confidence reading is
    found early, remaining frames are skipped (early-exit optimisation).

    Each frame is cropped to the reading zone (or heuristic bib area), then
    processed at ``cfg.zoom_scale`` *and* every scale in ``cfg.extra_scales``.
    """
    engine = get_ocr_engine(cfg.engine)
    readings: list[tuple[str, float]] = []

    # Build the complete set of scales to try, deduplicated and sorted.
    all_scales: list[float] = sorted(set([cfg.zoom_scale] + list(cfg.extra_scales)))

    # ── Approach-first ordering ────────────────────────────────────
    # Pre-crossing frames (rider approaching, bib visible) come first,
    # ordered from furthest-before to just-before the line.
    # Post-crossing frames follow but are only used if approach frames
    # don't produce a confident enough result.
    if crossing_frame_index is not None:
        pre  = [f for f in burst_frames if f.index <= crossing_frame_index]
        post = [f for f in burst_frames if f.index >  crossing_frame_index]
        ordered_frames = pre + post   # pre already in chronological order
    else:
        ordered_frames = burst_frames

    for bf in ordered_frames:
        # Base crop at scale 1.0 – individual scale passes resize below
        base_crop = _crop_reading_zone(bf.image, reading_zone, detection, 1.0)
        if base_crop is None or base_crop.size == 0:
            continue

        for scale in all_scales:
            if scale <= 0:
                logger.warning("Skipping invalid OCR scale %.2f (must be > 0)", scale)
                continue
            if scale != 1.0:
                new_w = max(1, int(base_crop.shape[1] * scale))
                new_h = max(1, int(base_crop.shape[0] * scale))
                crop = cv2.resize(base_crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
            else:
                crop = base_crop

            try:
                ocr_out = engine.read(crop)
            except Exception as e:
                logger.warning("OCR error frame=%d scale=%.1f: %s", bf.index, scale, e)
                continue
            for text, conf in ocr_out:
                bib_str = _filter_digits(text, cfg.max_digits)
                if bib_str:
                    readings.append((bib_str, conf))

        # Early-exit: if any candidate already has accumulated confidence
        # above the threshold, no need to process post-crossing frames.
        if readings and crossing_frame_index is not None and bf.index < crossing_frame_index:
            interim = fuse_readings(readings)
            if interim.confidence >= cfg.confidence_threshold:
                logger.debug(
                    "OCR early-exit at frame %d  bib=%s  conf=%.3f",
                    bf.index, interim.bib, interim.confidence,
                )
                result = interim
                logger.info(
                    "OCR result (early)  bib=%s  conf=%.3f  readings=%d  scales=%s  candidates=%s",
                    result.bib, result.confidence, len(readings), all_scales, result.candidates,
                )
                return result

    result = fuse_readings(readings)
    logger.info(
        "OCR result  bib=%s  conf=%.3f  readings=%d  scales=%s  candidates=%s",
        result.bib, result.confidence, len(readings), all_scales, result.candidates,
    )
    return result
