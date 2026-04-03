"""
UniFi Protect API client management.
Provides a lazily-initialised, thread-safe singleton client with auto-reconnect.
"""

import logging

from uiprotect import ProtectApiClient

from app.config import PROTECT_PASSWORD, PROTECT_PORT, PROTECT_USERNAME
from app.database import get_setting

logger = logging.getLogger(__name__)

_protect_client: ProtectApiClient | None = None

try:
    import asyncio
    _protect_client_lock = asyncio.Lock()
except RuntimeError:
    # During import before an event loop exists (e.g. test collection) this is fine;
    # the lock is created lazily on first use inside an async context.
    _protect_client_lock = None  # type: ignore[assignment]


def _get_lock():
    import asyncio

    global _protect_client_lock
    if _protect_client_lock is None:
        _protect_client_lock = asyncio.Lock()
    return _protect_client_lock


def get_protect_host() -> str:
    """Return the Protect NVR hostname from settings (or the env-var default)."""
    from app.config import PROTECT_HOST

    host = get_setting("protect_host", "")
    return host or PROTECT_HOST


async def get_protect_client(force_reconnect: bool = False) -> ProtectApiClient:
    """Return (or create) the singleton Protect API client."""
    global _protect_client

    host = get_protect_host()
    if not host:
        raise ValueError("Protect host not configured. Set it in Settings.")

    async with _get_lock():
        if _protect_client is None or force_reconnect:
            if _protect_client is not None:
                try:
                    await _protect_client.close()
                except Exception:
                    pass

            logger.info("Connecting to UniFi Protect at %s", host)
            _protect_client = ProtectApiClient(
                host=host,
                port=PROTECT_PORT,
                username=PROTECT_USERNAME,
                password=PROTECT_PASSWORD,
                verify_ssl=False,
            )
            await _protect_client.update()
            logger.info("Connected to UniFi Protect")
        else:
            try:
                _ = _protect_client.bootstrap.nvr.name
            except Exception as exc:
                logger.warning("Protect client stale, reconnecting: %s", exc)
                try:
                    await _protect_client.close()
                except Exception:
                    pass
                _protect_client = ProtectApiClient(
                    host=host,
                    port=PROTECT_PORT,
                    username=PROTECT_USERNAME,
                    password=PROTECT_PASSWORD,
                    verify_ssl=False,
                )
                await _protect_client.update()
                logger.info("Reconnected to UniFi Protect")

        return _protect_client


async def close_protect_client() -> None:
    """Close the singleton client on shutdown."""
    global _protect_client
    if _protect_client:
        await _protect_client.close()
        _protect_client = None


def invalidate_protect_client() -> None:
    """Force next call to get_protect_client() to create a new connection."""
    global _protect_client
    _protect_client = None
