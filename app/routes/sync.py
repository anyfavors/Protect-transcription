"""
Sync historical speech events from the UniFi Protect NVR.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.config import LOCAL_TZ
from app.database import get_connection, get_settings
from app.protect import get_protect_client, get_protect_host
from app.worker import queue_transcription

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/sync")
async def sync_speech_events(
    hours: int = Query(default=24, ge=1, le=720),
):
    """Fetch speech events from Protect for the last *hours* hours and queue any missing ones."""
    host = get_protect_host()
    if not host:
        raise HTTPException(
            status_code=400, detail="Protect host not configured. Set it in Settings."
        )

    try:
        client = await get_protect_client()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cannot connect to Protect: {exc}") from exc

    end_time = datetime.now(tz=LOCAL_TZ)
    start_time = end_time - timedelta(hours=hours)
    logger.info("Syncing speech events %s → %s", start_time.isoformat(), end_time.isoformat())

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT event_id FROM transcriptions")
    existing_events = {row[0] for row in cursor.fetchall()}
    conn.close()

    settings = get_settings()
    language = settings.get("language", "da")

    events_found = events_queued = events_skipped = events_live_skipped = speech_events_found = 0
    all_smart_types: set[str] = set()
    all_event_types: set[str] = set()
    errors: list[str] = []

    try:
        events = await client.get_events(start=start_time, end=end_time)

        for event in events:
            events_found += 1
            event_type = getattr(event, "type", None)
            if event_type:
                all_event_types.add(str(event_type))

            smart_detect_types = getattr(event, "smart_detect_types", None)
            if smart_detect_types is None:
                continue

            smart_types_str = []
            for t in smart_detect_types:
                if hasattr(t, "value"):
                    s = str(t.value).lower()
                elif hasattr(t, "name"):
                    s = str(t.name).lower()
                else:
                    s = str(t).lower()
                smart_types_str.append(s)
                all_smart_types.add(s)

            if not any(s in {"alrmspeak", "speech", "speechdetect"} for s in smart_types_str):
                continue

            speech_events_found += 1

            if getattr(event, "end", None) is None:
                events_live_skipped += 1
                continue

            event_id = str(event.id)
            if event_id in existing_events:
                events_skipped += 1
                continue

            camera_id = getattr(event, "camera_id", None)
            if not camera_id:
                cam = getattr(event, "camera", None)
                camera_id = cam.id if cam else None
            if not camera_id:
                continue

            event_time = getattr(event, "start", None)
            if not event_time:
                continue
            timestamp_ms = int(event_time.timestamp() * 1000)

            camera = client.bootstrap.cameras.get(camera_id)
            camera_name: str = (
                (camera.name or f"Unknown ({camera_id})") if camera else f"Unknown ({camera_id})"
            )

            if queue_transcription(event_id, str(camera_id), camera_name, timestamp_ms, language):
                events_queued += 1
                existing_events.add(event_id)

    except AttributeError as exc:
        errors.append(f"API method not available: {exc}")
        logger.error(errors[-1])
    except Exception as exc:
        errors.append(f"Error fetching events: {exc}")
        logger.exception(errors[-1])

    logger.info(
        "Sync done: found=%d speech=%d queued=%d skipped=%d live_skipped=%d",
        events_found,
        speech_events_found,
        events_queued,
        events_skipped,
        events_live_skipped,
    )

    result: dict = {
        "status": "completed",
        "hours_searched": hours,
        "events_found": events_found,
        "speech_events_found": speech_events_found,
        "events_queued": events_queued,
        "events_skipped": events_skipped,
        "events_live_skipped": events_live_skipped,
        "message": f"Queued {events_queued} new events for transcription",
        "debug_smart_types": list(all_smart_types),
        "debug_event_types": list(all_event_types),
    }
    if errors:
        result["errors"] = errors
        result["status"] = "completed_with_errors"

    return result
