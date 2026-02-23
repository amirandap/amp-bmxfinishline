# Grassroot Finishline Backend

A production-ready Python backend that processes bicycle race video (720p/1080p, 30 fps), detects finish-line crossings, reads front bib numbers via OCR, generates an arrival ranking by timestamp, and exposes a REST API for calibration, processing control, arrivals, manual corrections, and export.

## Architecture

```
┌────────────┐     ┌──────────────┐     ┌────────────┐
│ FrameSource│────▶│ YOLO + Track │────▶│  Crossing   │
│ (MP4/RTMP) │     │  (in ROI)    │     │  Detector   │
└────────────┘     └──────────────┘     └─────┬──────┘
                                              │ FinishEvent
                                        ┌─────▼──────┐
                                        │   Burst     │
                                        │   Capture   │
                                        └─────┬──────┘
                                        ┌─────▼──────┐
                                        │  OCR + Fuse │
                                        │  (PaddleOCR)│
                                        └─────┬──────┘
                                        ┌─────▼──────┐
                                        │  Persist    │
                                        │  (SQLite/PG)│
                                        └─────┬──────┘
                                        ┌─────▼──────┐
                                        │  REST API   │
                                        │  (FastAPI)  │
                                        └────────────┘
```

### Module layout

| Module | Responsibility |
|---|---|
| `app/core/ingest.py` | `FrameSource` abstraction (MP4 via OpenCV, RTMP via FFmpeg) |
| `app/core/detection.py` | YOLO detection + ByteTrack tracking, ROI masking |
| `app/core/crossing.py` | Finish-line crossing logic with cooldown & dedup |
| `app/core/burst.py` | Ring buffer + evidence persistence |
| `app/core/ocr.py` | Bib crop, PaddleOCR, confidence-weighted fusion |
| `app/core/geometry.py` | Point-in-polygon, side-of-line, polygon mask |
| `app/core/pipeline.py` | End-to-end orchestrator (background thread) |
| `app/api/` | FastAPI routers for races, calibration, processing, arrivals, export |
| `app/models/` | SQLAlchemy ORM models, DB session management |
| `app/schemas/` | Pydantic request/response schemas |

---

## Quick Start (Local – MP4 file)

### 1. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> **GPU**: If you have a CUDA GPU, install the GPU version of PaddlePaddle and set `device: "cuda:0"` in `config.yaml`.

### 2. Start the server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Create a race

```bash
curl -X POST http://localhost:8000/races \
  -H "Content-Type: application/json" \
  -d '{"name": "Sunday Crit Race"}'
```

Response: `{"id": "a1b2c3d4e5f6", "name": "Sunday Crit Race", ...}`

### 4. Set calibration

You need to know the pixel coordinates for your camera setup.

```bash
RACE_ID="a1b2c3d4e5f6"

curl -X POST http://localhost:8000/races/$RACE_ID/calibration \
  -H "Content-Type: application/json" \
  -d '{
    "roi_polygon": [[100,100],[1800,100],[1800,900],[100,900]],
    "finish_line": [[960,100],[960,900]],
    "reading_zone": [[400,200],[700,200],[700,600],[400,600]]
  }'
```

**Tips:**
- `roi_polygon` – define the region where riders appear (avoids false detections from spectators, etc.)
- `finish_line` – the two endpoints of the finish line in the video frame
- `reading_zone` (optional) – rectangle where bib numbers are typically visible; improves OCR accuracy

### 5. Start processing an MP4

```bash
curl -X POST http://localhost:8000/races/$RACE_ID/process/start \
  -H "Content-Type: application/json" \
  -d '{"input_type": "file", "input": "/path/to/race_video.mp4"}'
```

### 6. Check status

```bash
curl http://localhost:8000/races/$RACE_ID/process/status
```

### 7. View arrivals

```bash
curl http://localhost:8000/races/$RACE_ID/arrivals
```

### 8. Export results

```bash
# CSV
curl -o results.csv http://localhost:8000/races/$RACE_ID/export.csv

# JSON
curl http://localhost:8000/races/$RACE_ID/export.json
```

---

## Quick Start (RTMP Stream)

### Prerequisites

- FFmpeg installed on the host (or use the Docker image which includes it)
- An RTMP server (e.g., Nginx-RTMP, OBS streaming to `rtmp://host/live/stream`)

### Start processing

```bash
curl -X POST http://localhost:8000/races/$RACE_ID/process/start \
  -H "Content-Type: application/json" \
  -d '{"input_type": "rtmp", "input": "rtmp://your-server/live/stream"}'
```

The backend invokes FFmpeg as a subprocess to consume the RTMP stream and pipe raw frames. Ensure the FFmpeg binary is accessible in `$PATH`.

### Stop processing

```bash
curl -X POST http://localhost:8000/races/$RACE_ID/process/stop
```

---

## Manual Corrections

### Edit a bib number

```bash
curl -X PATCH http://localhost:8000/races/$RACE_ID/arrivals/$ARRIVAL_ID \
  -H "Content-Type: application/json" \
  -d '{"bib": "42", "status": "MANUAL_OK"}'
```

### Reorder arrivals

```bash
curl -X POST http://localhost:8000/races/$RACE_ID/arrivals/reorder \
  -H "Content-Type: application/json" \
  -d '{"arrival_ids_in_order": ["id3", "id1", "id2"]}'
```

### Merge duplicate arrivals

```bash
curl -X POST http://localhost:8000/races/$RACE_ID/arrivals/merge \
  -H "Content-Type: application/json" \
  -d '{"keep_id": "id1", "merge_id": "id2", "bib": "7"}'
```

---

## Docker

### Build and run (SQLite)

```bash
docker compose up --build
```

### With Postgres

Uncomment the `postgres` service and `DATABASE_URL` environment variable in `docker-compose.yml`, then:

```bash
docker compose up --build
```

---

## Configuration

All settings are in `config.yaml`. Override any value with environment variables prefixed `FINISHLINE_`:

| Env var | Example | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host/db` | Database connection string |
| `FINISHLINE_YOLO_MODEL` | `yolov8s.pt` | YOLO model name/path |
| `FINISHLINE_YOLO_CONFIDENCE` | `0.5` | Detection confidence threshold |
| `FINISHLINE_CROSSING_COOLDOWN_SECONDS` | `3.0` | Min seconds between finish events |
| `FINISHLINE_BURST_FRAMES_BEFORE` | `15` | Frames to capture before crossing |
| `FINISHLINE_OCR_ZOOM_SCALE` | `3.0` | Digital zoom factor for bib crops |

---

## Running Tests

```bash
pip install pytest httpx pytest-asyncio
pytest -v
```

Tests cover:
- **Geometry**: point-in-polygon, side-of-line, crossing detection
- **OCR fusion**: weighted voting, tie-breaking, edge cases
- **Crossing detector**: single crossing, dedup, cooldown

---

## Web UI

A lightweight dashboard is available at the root path (`/`). It allows you to create and select races, enter calibration JSON, start/stop processing, view current status, and manage arrivals. The UI remains a simple vanilla interface but is now managed with [Vite](https://vitejs.dev/) for better development experience.

### Frontend development

The UI source lives under `app/frontend`. To start a dev server with hot reload:

```bash
cd app/frontend
npm install      # only needed first time or when package.json changes
npm run dev
```

Open `http://localhost:5173/` in your browser during development; API requests will be proxied to the backend (start the FastAPI app separately on port 8000).

To build production assets which FastAPI serves from `/static`:

```bash
cd app/frontend
npm run build
```

Built files are emitted to `app/frontend/static` and will be automatically mounted by the backend.


## Quick Start (Local – MP4 file)

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/races` | Create a race |
| `GET` | `/races/{id}` | Get race details |
| `GET` | `/races/{id}/calibration` | Get calibration |
| `POST` | `/races/{id}/calibration` | Set calibration |
| `POST` | `/races/{id}/process/start` | Start video processing |
| `POST` | `/races/{id}/process/stop` | Stop processing |
| `GET` | `/races/{id}/process/status` | Processing status |
| `GET` | `/races/{id}/arrivals` | List arrivals (sorted) |
| `PATCH` | `/races/{id}/arrivals/{aid}` | Edit bib/status |
| `POST` | `/races/{id}/arrivals/reorder` | Manual reorder |
| `POST` | `/races/{id}/arrivals/merge` | Merge duplicates |
| `GET` | `/races/{id}/export.csv` | Export CSV |
| `GET` | `/races/{id}/export.json` | Export JSON |
| `GET` | `/health` | Health check |

Interactive docs: `http://localhost:8000/docs`

---

## Known Limitations & Tips

1. **Camera position**: calibration is per-race; always update `roi_polygon` and `finish_line` when the camera moves.
2. **Bib readability**: OCR accuracy depends heavily on bib size, font, and camera angle. Use `reading_zone` to constrain the search area and increase `zoom_scale` for distant cameras.
3. **Group finishes**: with 2–3 riders finishing together, the tracker may briefly lose IDs. The cooldown parameter helps prevent duplicates; use the merge API for remaining issues.
4. **YOLO model**: the default `yolov8n.pt` (nano) is fast but less accurate. For better detection, try `yolov8s.pt` or `yolov8m.pt`.
5. **RTMP latency**: FFmpeg piping adds ~1–2 seconds of latency. For live timing this is acceptable at grassroots level.
6. **CPU vs GPU**: all defaults are CPU-friendly. For faster processing, use a CUDA GPU and set `device: "cuda:0"` in config.
7. **SQLite concurrency**: SQLite handles one writer at a time. For multi-race concurrent processing, switch to Postgres.
