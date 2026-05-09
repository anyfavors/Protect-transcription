"""
UniFi Protect Alarm Manager webhook receiver.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import verify_webhook_signature
from app.database import get_settings
from app.metrics import inc
from app.protect import get_protect_client
from app.util import camera_display_name, find_camera
from app.worker import queue_transcription

logger = logging.getLogger(__name__)
router = APIRouter()

_SPEECH_KEYS = {"speech", "voice", "talking", "audio_alarm_speak", "alrmspeak"}


@router.post("/api/webhook")
async def receive_webhook(
    request: Request,
    body: bytes = Depends(verify_webhook_signature),
):
    """
    Receive a webhook from the UniFi Protect Alarm Manager.

    Expected payload::

        {
            "alarm": {
                "triggers": [{"key": "speech", "device": "CAMERA_MAC", "timestamp": ...}]
            },
            "timestamp": 1234567890
        }
    """
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc

    inc("webhooks_received_total")
    logger.info("Received webhook: %s", payload)

    alarm = payload.get("alarm", {})
    timestamp = payload.get("timestamp", 0)
    triggers = alarm.get("triggers", [])

    settings = get_settings()
    language = settings.get("language", "da")

    events_queued = 0
    failures: list[str] = []

    for trigger in triggers:
        trigger_key = (trigger.get("key") or "").lower()
        camera_id = trigger.get("device", "")
        event_id_from_protect = trigger.get("eventId", "")
        trigger_timestamp = trigger.get("timestamp", timestamp)

        if trigger_key not in _SPEECH_KEYS:
            logger.debug("Ignoring non-speech trigger: %s", trigger_key)
            continue

        event_id = event_id_from_protect or f"{camera_id}_{trigger_timestamp}_{trigger_key}"
        logger.info(
            "Speech event detected: key=%s camera=%s event_id=%s",
            trigger_key,
            camera_id,
            event_id,
        )

        try:
            client = await get_protect_client()
            camera = find_camera(client, camera_id)
            camera_name = camera_display_name(camera, camera_id)
        except Exception:
            camera_name = f"Unknown ({camera_id})"

        try:
            if queue_transcription(event_id, camera_id, camera_name, trigger_timestamp, language):
                events_queued += 1
        except Exception as exc:
            logger.exception("queue_transcription failed for %s", event_id)
            failures.append(f"{event_id}: {exc}")

    if failures:
        return {
            "status": "partial",
            "queued": events_queued,
            "failures": failures,
        }

    return {"status": "accepted", "queued": events_queued}
