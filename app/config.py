"""Configuration loader – YAML + env-var overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_CONFIG_PATH = os.getenv("FINISHLINE_CONFIG", "config.yaml")


# ── Pydantic sub-models ────────────────────────────────────────────

class DatabaseConfig(BaseModel):
    url: str = "sqlite:///data/finishline.db"


class YoloConfig(BaseModel):
    # Recommended upgrade: swap to "rtdetr-l.pt" (RT-DETRv2 via ultralytics)
    # for better accuracy at similar speed.  No code changes needed – just a
    # config swap.  Full transformer variant: "rtdetr-x.pt".
    model: str = "yolov8n.pt"
    confidence: float = 0.40
    iou: float = 0.45
    # COCO class IDs: 0=person, 1=bicycle.  Include both so the bicycle bbox
    # bottom-center (front-wheel proxy) can be used as the crossing point.
    classes: list[int] = Field(default_factory=lambda: [0, 1])
    device: str = ""


class TrackerConfig(BaseModel):
    type: str = "bytetrack"
    track_high_thresh: float = 0.5
    track_low_thresh: float = 0.1
    new_track_thresh: float = 0.6
    track_buffer: int = 30
    match_thresh: float = 0.8


class CrossingConfig(BaseModel):
    cooldown_seconds: float = 2.0
    # Direction filter – only count crossings in one direction.
    # "any"        – both directions (default, safest for initial setup)
    # "pos_to_neg" – only count when side goes positive→negative (left of line → right)
    # "neg_to_pos" – only count when side goes negative→positive (right of line → left)
    crossing_direction: str = "any"
    # Which point on the detection box to use for crossing.
    # "bottom_center" – foot/wheel contact point (best for upright riders)
    # "center"        – centre of box
    # "top_center"    – top edge mid-point
    # "front_left"    – bottom-left corner  (use when riders move right-to-left)
    # "front_right"   – bottom-right corner (use when riders move left-to-right)
    crossing_point: str = "bottom_center"
    # Interpolate the crossing timestamp between the frame before and after
    # the line; reduces timing error to sub-frame precision.
    interpolate_timestamp: bool = True


class BurstConfig(BaseModel):
    frames_before: int = 10
    frames_after: int = 10


class OcrConfig(BaseModel):
    engine: str = "paddleocr"
    confidence_threshold: float = 0.55
    zoom_scale: float = 2.0
    max_digits: int = 3
    # Additional scales to try for each crop – empty means only zoom_scale.
    # e.g. [0.5, 1.0, 1.5, 2.0] tries the crop at four scales, then fuses results.
    extra_scales: list[float] = Field(default_factory=lambda: [0.5, 1.0, 1.5])


class CameraConfig(BaseModel):
    check_interval: int = 100  # frames between camera movement checks
    displacement_threshold: float = 5.0  # avg px displacement to flag movement


class OutputConfig(BaseModel):
    base_dir: str = "data/outputs"


class AppConfig(BaseModel):
    database: DatabaseConfig = DatabaseConfig()
    yolo: YoloConfig = YoloConfig()
    tracker: TrackerConfig = TrackerConfig()
    crossing: CrossingConfig = CrossingConfig()
    burst: BurstConfig = BurstConfig()
    ocr: OcrConfig = OcrConfig()
    camera: CameraConfig = CameraConfig()
    output: OutputConfig = OutputConfig()


# ── Loader ──────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _env_overrides() -> dict[str, Any]:
    """Read FINISHLINE_* env vars and map them to nested dict keys.

    Example: FINISHLINE_YOLO_MODEL=yolov8s.pt  →  {"yolo": {"model": "yolov8s.pt"}}
    """
    prefix = "FINISHLINE_"
    out: dict[str, Any] = {}
    for key, val in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("_", 1)
        if len(parts) == 2:
            section, field = parts
            out.setdefault(section, {})[field] = val
        else:
            out[parts[0]] = val
    return out


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path or _CONFIG_PATH)
    raw: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    raw = _deep_merge(raw, _env_overrides())

    # Handle DATABASE_URL shortcut
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        raw.setdefault("database", {})["url"] = db_url

    return AppConfig(**raw)


# Singleton
_cfg: AppConfig | None = None


def get_config() -> AppConfig:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg
