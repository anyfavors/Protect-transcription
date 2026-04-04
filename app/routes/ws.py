"""
WebSocket endpoint for real-time transcription updates.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.broadcast import register, unregister

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Accept a WebSocket connection and keep it alive.

    The server pushes events via the broadcast module whenever a transcription
    changes status (completed, error, filtered).  The client doesn't need to
    send anything — we just keep reading to detect disconnects.
    """
    await websocket.accept()
    register(websocket)
    logger.debug("WebSocket client connected")
    try:
        while True:
            # Wait for client messages (ping/pong or disconnect detection)
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        unregister(websocket)
        logger.debug("WebSocket client disconnected")
