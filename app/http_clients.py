"""
Shared httpx.AsyncClient instances.

Reusing a single client per upstream avoids a fresh TLS handshake (and TCP
connection) for every request. The clients are created lazily on first use
and closed on app shutdown.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_whisper_client: httpx.AsyncClient | None = None
_ollama_client: httpx.AsyncClient | None = None


def get_whisper_client() -> httpx.AsyncClient:
    """Long-lived client for the speaches server (300s timeout for transcription)."""
    global _whisper_client
    if _whisper_client is None or _whisper_client.is_closed:
        _whisper_client = httpx.AsyncClient(timeout=300.0)
    return _whisper_client


def get_ollama_client() -> httpx.AsyncClient:
    """Long-lived client for Ollama (120s timeout for generation)."""
    global _ollama_client
    if _ollama_client is None or _ollama_client.is_closed:
        _ollama_client = httpx.AsyncClient(timeout=120.0)
    return _ollama_client


async def close_clients() -> None:
    global _whisper_client, _ollama_client
    for c in (_whisper_client, _ollama_client):
        if c is not None and not c.is_closed:
            try:
                await c.aclose()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Error closing httpx client: %s", exc)
    _whisper_client = None
    _ollama_client = None
