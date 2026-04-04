"""
WebSocket broadcast hub for real-time updates.

Maintains a set of connected WebSocket clients and broadcasts events to all of them.
Used by the transcription worker to push status updates to the UI.
"""

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_clients: set[WebSocket] = set()


def register(ws: WebSocket) -> None:
    """Add a WebSocket client to the broadcast set."""
    _clients.add(ws)


def unregister(ws: WebSocket) -> None:
    """Remove a WebSocket client from the broadcast set."""
    _clients.discard(ws)


async def broadcast(event: dict) -> None:
    """Send an event dict as JSON to all connected WebSocket clients."""
    for ws in list(_clients):
        try:
            await ws.send_json(event)
        except Exception:
            _clients.discard(ws)
