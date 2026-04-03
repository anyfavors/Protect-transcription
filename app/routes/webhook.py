import logging

from fastapi import APIRouter, HTTPException, Request

from app.database import get_settings
from app.protect import get_protect_client
from app.worker import queue_transcription

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/webhook")
async def receive_webhook(request: Request):
    """
    Receive webhook from UniFi Protect Alarm Manager.

    Expected payload::

        {
            "alarm": {
                "triggers": [{"key": "speech", "device": "CAMERA_MAC", "timestamp": 1234567890}]
            },
            "timestamp": 1234567890
        }
    """
    try:
        payload = await request.json()
        logger.info("Received webhook: %s", payload)

        alarm = payload.get("alarm", {})
        timestamp = payload.get("timestamp", 0)
        triggers = alarm.get("triggers", [])

        settings = get_settings()
        language = settings.get("language", "da")
        events_queued = 0

        _speech_keys = {"speech", "voice", "talking", "audio_alarm_speak", "alrmspeak"}

        for trigger in triggers:
            trigger_key = trigger.get("key", "")
            camera_id = trigger.get("device", "")
            event_id_from_protect = trigger.get("eventId", "")
            trigger_timestamp = trigger.get("timestamp", timestamp)

            if trigger_key.lower() not in _speech_keys:
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
                camera = client.bootstrap.cameras.get(camera_id)
                if not camera:
                    normalized_mac = camera_id.upper().replace(":", "").replace("-", "")
                    for cam in client.bootstrap.cameras.values():
                        if cam.mac.upper().replace(":", "").replace("-", "") == normalized_mac:
                            camera = cam
                            break
                camera_name: str = (
                    (camera.name or f"Unknown ({camera_id})")
                    if camera
                    else f"Unknown ({camera_id})"
                )
            except Exception:
                camera_name = f"Unknown ({camera_id})"

            if queue_transcription(event_id, camera_id, camera_name, trigger_timestamp, language):
                events_queued += 1

        return {"status": "accepted", "message": f"Webhook received, {events_queued} events queued"}

    except Exception as exc:
        logger.exception("Error processing webhook: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
