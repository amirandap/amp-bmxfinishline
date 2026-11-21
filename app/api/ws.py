"""WebSocket connection manager for live pipeline log streaming."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


class _ConnectionManager:
    """Maintains active WebSocket connections keyed by race_id."""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, race_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(race_id, []).append(ws)
        logger.info("WS connected  race=%s  total=%d", race_id, len(self._connections[race_id]))

    def disconnect(self, race_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(race_id, [])
        if ws in conns:
            conns.remove(ws)
        logger.info("WS disconnected  race=%s  remaining=%d", race_id, len(conns))

    async def broadcast(self, race_id: str, data: dict[str, Any]) -> None:
        """Broadcast *data* as JSON to all clients watching *race_id*."""
        conns = list(self._connections.get(race_id, []))
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(race_id, ws)

    def broadcast_sync(self, race_id: str, data: dict[str, Any]) -> None:
        """Thread-safe broadcast: schedule coroutine on the running event loop.

        Called from the pipeline background thread (non-async context).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(race_id, data), loop)


# Singleton used across the application
manager = _ConnectionManager()


# ── WebSocket endpoint ─────────────────────────────────────────────

@router.websocket("/races/{race_id}/ws/log")
async def ws_log(race_id: str, websocket: WebSocket) -> None:
    """Stream pipeline events to the browser in real time.

    Message types
    -------------
    status    – periodic heartbeat with ``{state, frames_processed, arrivals_detected, camera_moved}``
    arrival   – fired when a new arrival is persisted ``{id, bib, confidence, position, timestamp_ms, status}``
    error     – pipeline error ``{message}``
    """
    await manager.connect(race_id, websocket)
    try:
        # Send initial status immediately
        from app.core.pipeline import get_pipeline
        pipe = get_pipeline(race_id)
        if pipe:
            s = pipe.status
            await websocket.send_json({
                "type": "status",
                "state": s.state,
                "frames_processed": s.frames_processed,
                "arrivals_detected": s.arrivals_detected,
                "camera_moved": s.camera_moved,
            })

        # Keep connection alive; the pipeline will push events via manager
        while True:
            # Receive any client messages (ping-pong / keep-alive)
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if msg == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send a heartbeat so the browser knows we're alive
                pipe = get_pipeline(race_id)
                if pipe:
                    s = pipe.status
                    await websocket.send_json({
                        "type": "status",
                        "state": s.state,
                        "frames_processed": s.frames_processed,
                        "arrivals_detected": s.arrivals_detected,
                        "camera_moved": s.camera_moved,
                    })
                else:
                    await websocket.send_json({"type": "status", "state": "idle"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WS error race=%s: %s", race_id, exc)
    finally:
        manager.disconnect(race_id, websocket)
