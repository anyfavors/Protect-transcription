"""
Settings GET/PUT and connectivity test endpoints.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.config import AVAILABLE_LANGUAGES, AVAILABLE_MODELS, WHISPER_URL
from app.database import get_settings, save_setting
from app.protect import get_protect_client, get_protect_host, invalidate_protect_client

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_KEYS = {
    "whisper_model",
    "language",
    "buffer_before",
    "buffer_after",
    "vad_filter",
    "beam_size",
    "protect_host",
    "ollama_url",
    "ollama_model",
    "condition_on_previous_text",
    "no_speech_threshold",
    "compression_ratio_threshold",
}

_INT_BOUNDS = {
    "buffer_before": (1, 60),
    "buffer_after": (1, 600),
    "beam_size": (1, 10),
}


@router.get("/api/settings")
async def api_get_settings():
    return {
        "settings": get_settings(),
        "available_models": AVAILABLE_MODELS,
        "available_languages": AVAILABLE_LANGUAGES,
    }


@router.put("/api/settings")
async def api_update_settings(request: Request):
    data = await request.json()
    updated = []
    protect_host_changed = False

    for key, value in data.items():
        if key not in _ALLOWED_KEYS:
            continue

        if key in _INT_BOUNDS:
            lo, hi = _INT_BOUNDS[key]
            try:
                int_val = int(value)
                if not (lo <= int_val <= hi):
                    raise ValueError(f"{key} must be between {lo} and {hi}")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        if key == "vad_filter":
            value = "true" if value in (True, "true", "1", 1) else "false"

        if key == "protect_host":
            value = str(value).strip().rstrip("/").removeprefix("https://").removeprefix("http://")
            protect_host_changed = True

        save_setting(key, str(value))
        updated.append(key)

    if protect_host_changed:
        invalidate_protect_client()
        logger.info("Protect host changed, client will reconnect on next request")

    return {"status": "updated", "updated_keys": updated, "settings": get_settings()}


@router.post("/api/settings/test-whisper")
async def test_whisper_connection():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{WHISPER_URL}/v1/models")
            if response.status_code == 200:
                return {
                    "status": "connected",
                    "whisper_url": WHISPER_URL,
                    "models": response.json(),
                }
            return {"status": "error", "message": f"Whisper returned status {response.status_code}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/settings/test-protect")
async def test_protect_connection():
    try:
        host = get_protect_host()
        if not host:
            return {"status": "error", "message": "Protect host not configured"}
        client = await get_protect_client(force_reconnect=True)
        nvr = client.bootstrap.nvr
        cameras = list(client.bootstrap.cameras.values())
        return {
            "status": "connected",
            "host": host,
            "nvr_name": nvr.name,
            "nvr_version": str(nvr.version),
            "camera_count": len(cameras),
            "cameras": [{"id": c.id, "name": c.name} for c in cameras],
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
