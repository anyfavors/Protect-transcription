"""
WebSocket broadcast hub for real-time updates.

Maintains a set of connected WebSocket clients and broadcasts events to all of them.
Used by the transcription worker to push status updates to the UI.

Sends to all clients concurrently with ``asyncio.gather`` so a single slow
client cannot block the others.
"""

import asyncio
import logging

from fastapi import WebSocket

from app.metrics import set_gauge

logger = logging.getLogger(__name__)

_clients: set[WebSocket] = set()


def register(ws: WebSocket) -> None:
    """Add a WebSocket client to the broadcast set."""
    _clients.add(ws)
    set_gauge("ws_clients_connected", len(_clients))


def unregister(ws: WebSocket) -> None:
    """Remove a WebSocket client from the broadcast set."""
    _clients.discard(ws)
    set_gauge("ws_clients_connected", len(_clients))


async def _send_one(ws: WebSocket, event: dict) -> bool:
    try:
        await ws.send_json(event)
        return True
    except Exception:
        return False


async def broadcast(event: dict) -> None:
    """Send an event dict as JSON to all connected WebSocket clients in parallel."""
    if not _clients:
        return
    targets = list(_clients)
    results = await asyncio.gather(
        *(_send_one(ws, event) for ws in targets), return_exceptions=False
    )
    for ws, ok in zip(targets, results, strict=True):
        if not ok:
            _clients.discard(ws)
    set_gauge("ws_clients_connected", len(_clients))
