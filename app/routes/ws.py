"""
WebSocket endpoint for real-time transcription updates.

Adds a periodic server-side ping so the connection survives reverse-proxy idle
timeouts (k3s ingress, Cloudflare, etc).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.broadcast import register, unregister

logger = logging.getLogger(__name__)
router = APIRouter()

_PING_INTERVAL_SECONDS = 25


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    register(websocket)
    logger.debug("WebSocket client connected")

    async def _pinger() -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL_SECONDS)
                await websocket.send_json({"type": "ping"})
        except Exception:
            pass

    ping_task = asyncio.create_task(_pinger())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket error: %s", exc)
    finally:
        ping_task.cancel()
        unregister(websocket)
        logger.debug("WebSocket client disconnected")
