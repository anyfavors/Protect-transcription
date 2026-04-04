"""
Background transcription worker, audio compression worker, and queue helpers.
"""

import asyncio
import json
import logging
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from app.config import AUDIO_PATH, DATABASE_PATH, LOCAL_TZ
from app.database import get_settings
from app.transcription import (
    compute_audio_rms,
    fetch_audio_clip,
    save_audio_file,
    transcribe_audio,
)

logger = logging.getLogger(__name__)


def queue_transcription(
    event_id: str,
    camera_id: str,
    camera_name: str,
    timestamp_ms: int,
    language: str = "da",
) -> bool:
    """
    Insert a pending transcription row.
    Returns True if queued, False if the event already exists.
    """
    conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM transcriptions WHERE event_id = ?", (event_id,))
        if cursor.fetchone():
            logger.debug("Event %s already exists, skipping", event_id)
            return False

        event_time = datetime.fromtimestamp(timestamp_ms / 1000, tz=LOCAL_TZ)
        cursor.execute(
            """
            INSERT INTO transcriptions
                (event_id, camera_id, camera_name, timestamp, status, language)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (event_id, camera_id, camera_name, event_time.isoformat(), language),
        )
        conn.commit()
        logger.info("Queued transcription for event %s from %s", event_id, camera_name)
        return True

    except sqlite3.IntegrityError:
        logger.debug("Event %s already exists (integrity error)", event_id)
        return False
    finally:
        conn.close()


async def process_pending_transcription(row: dict) -> None:
    """Fetch audio, transcribe, and persist the result for one pending row."""
    event_id = row["event_id"]
    camera_id = row["camera_id"]
    camera_name = row["camera_name"]
    timestamp_str = row["timestamp"]
    record_id = row["id"]

    conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()

    try:
        settings = get_settings()
        buffer_before = int(settings.get("buffer_before", "5"))
        buffer_after = int(settings.get("buffer_after", "60"))

        try:
            event_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=LOCAL_TZ)
        except Exception as exc:
            raise ValueError(f"Invalid timestamp: {timestamp_str}") from exc

        start_time = event_time - timedelta(seconds=buffer_before)
        end_time = event_time + timedelta(seconds=buffer_after)

        logger.info(
            "Processing event %s from %s at %s (buffer -%ds +%ds)",
            event_id,
            camera_name,
            event_time.isoformat(),
            buffer_before,
            buffer_after,
        )

        # Reuse cached audio file if it exists (e.g. retranscribe-all flow).
        # Only fetch from the NVR when no cached file is available.
        existing_audio_file: str | None = row.get("audio_file")
        cached_path = Path(AUDIO_PATH) / existing_audio_file if existing_audio_file else None

        if cached_path and cached_path.exists():
            logger.info("Reusing cached audio file: %s", cached_path.name)
            audio_data = cached_path.read_bytes()
            audio_filename = existing_audio_file
        else:
            if existing_audio_file:
                logger.warning(
                    "Cached audio file missing (%s), re-fetching from NVR", existing_audio_file
                )
            fetched = await fetch_audio_clip(camera_id, start_time, end_time)
            if not fetched:
                cursor.execute(
                    "UPDATE transcriptions SET status='error', transcription='Failed to fetch audio' WHERE id=?",
                    (record_id,),
                )
                conn.commit()
                return
            audio_data = fetched
            audio_filename = save_audio_file(audio_data, event_time, camera_name)

        # ── Noise / silence pre-filter ──────────────────────────────
        min_energy = float(settings.get("min_audio_energy", "0.005"))
        if min_energy > 0:
            rms = compute_audio_rms(audio_data)
            if rms < min_energy:
                duration = len(audio_data) / (16000 * 2)
                logger.info(
                    "Audio filtered (RMS %.5f < %.5f) for event %s",
                    rms,
                    min_energy,
                    event_id,
                )
                cursor.execute(
                    """
                    UPDATE transcriptions
                    SET status='filtered',
                        transcription=?,
                        audio_file=?,
                        duration_seconds=?
                    WHERE id=?
                    """,
                    (
                        f"Silence/noise detected (RMS energy {rms:.5f} below threshold {min_energy})",
                        audio_filename,
                        duration,
                        record_id,
                    ),
                )
                conn.commit()
                await _broadcast_update(record_id, "filtered", camera_name, timestamp_str)
                return

        result = await transcribe_audio(audio_data)

        if "error" in result:
            cursor.execute(
                "UPDATE transcriptions SET status='error', transcription=?, audio_file=? WHERE id=?",
                (f"Transcription error: {result['error']}", audio_filename, record_id),
            )
        else:
            duration = len(audio_data) / (16000 * 2)  # 16 kHz, 16-bit
            segments = result.get("segments", [])
            segments_json = json.dumps(segments) if segments else None

            cursor.execute(
                """
                UPDATE transcriptions
                SET status='completed',
                    transcription=?,
                    segments=?,
                    language=?,
                    confidence=?,
                    audio_file=?,
                    duration_seconds=?
                WHERE id=?
                """,
                (
                    result.get("text", ""),
                    segments_json,
                    result.get("language", "da"),
                    result.get("confidence", 0),
                    audio_filename,
                    duration,
                    record_id,
                ),
            )

        conn.commit()
        logger.info("Completed event %s from %s", event_id, camera_name)

        final_status = "error" if "error" in result else "completed"
        await _broadcast_update(record_id, final_status, camera_name, timestamp_str)

    except Exception as exc:
        logger.exception("Error processing event %s: %s", event_id, exc)
        try:
            cursor.execute(
                "UPDATE transcriptions SET status='error', transcription=? WHERE id=?",
                (str(exc)[:500], record_id),
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


async def transcription_worker() -> None:
    """
    Infinite loop: atomically claim one pending row and process it.
    The SELECT + UPDATE happen in the same transaction so no other worker
    can steal the same row.
    """
    logger.info("Transcription worker started")

    while True:
        try:
            conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            row = None

            try:
                cursor.execute("""
                    SELECT id, event_id, camera_id, camera_name, timestamp, language, audio_file
                    FROM transcriptions
                    WHERE status = 'pending'
                    ORDER BY timestamp ASC
                    LIMIT 1
                """)
                row = cursor.fetchone()
                if row:
                    cursor.execute(
                        "UPDATE transcriptions SET status='processing' WHERE id=? AND status='pending'",
                        (row["id"],),
                    )
                    conn.commit()
                    if cursor.rowcount == 0:
                        row = None  # another worker claimed it
            finally:
                conn.close()

            if row:
                await process_pending_transcription(dict(row))
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(5)

        except Exception as exc:
            logger.exception("Error in transcription worker: %s", exc)
            await asyncio.sleep(10)


async def _broadcast_update(record_id: int, status: str, camera_name: str, timestamp: str) -> None:
    """Push a transcription status change to all connected WebSocket clients."""
    from app.broadcast import broadcast

    await broadcast(
        {
            "type": "transcription_update",
            "id": record_id,
            "status": status,
            "camera_name": camera_name,
            "timestamp": timestamp,
        }
    )


async def audio_compression_worker() -> None:
    """
    Periodically compress old WAV audio files to Opus/OGG.

    Runs hourly.  Controlled by the ``audio_compression_days`` setting:
    WAV files referenced by transcriptions older than N days are converted
    to OGG (libopus @ 32 kbps), the original is deleted, and the DB is updated.
    Set to 0 to disable.
    """
    logger.info("Audio compression worker started")

    while True:
        try:
            await asyncio.sleep(3600)

            settings = get_settings()
            days = int(settings.get("audio_compression_days", "7"))
            if days <= 0:
                continue

            conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, audio_file FROM transcriptions "
                "WHERE audio_file LIKE '%.wav' AND timestamp < datetime('now', ?)",
                (f"-{days} days",),
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                continue

            compressed = 0
            for row_id, audio_file in rows:
                wav_path = Path(AUDIO_PATH) / audio_file
                if not wav_path.exists():
                    continue

                ogg_name = audio_file.rsplit(".", 1)[0] + ".ogg"
                ogg_path = Path(AUDIO_PATH) / ogg_name

                try:
                    result = subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(wav_path),
                            "-c:a",
                            "libopus",
                            "-b:a",
                            "32k",
                            "-vbr",
                            "on",
                            str(ogg_path),
                        ],
                        capture_output=True,
                        timeout=60,
                    )
                except subprocess.TimeoutExpired:
                    logger.warning("ffmpeg timeout compressing %s", audio_file)
                    ogg_path.unlink(missing_ok=True)
                    continue

                if result.returncode == 0 and ogg_path.exists() and ogg_path.stat().st_size > 0:
                    wav_path.unlink(missing_ok=True)
                    conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute(
                        "UPDATE transcriptions SET audio_file=? WHERE id=?",
                        (ogg_name, row_id),
                    )
                    conn.commit()
                    conn.close()
                    compressed += 1
                else:
                    ogg_path.unlink(missing_ok=True)
                    logger.warning("Failed to compress %s (rc=%d)", audio_file, result.returncode)

            if compressed:
                logger.info("Compressed %d audio files to Opus/OGG", compressed)

        except Exception as exc:
            logger.exception("Audio compression worker error: %s", exc)
            await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Legacy helper kept for webhook / retry compatibility
# ---------------------------------------------------------------------------
async def process_speech_event(
    event_id: str,
    camera_id: str,
    timestamp_ms: int,
    skip_wait: bool = False,
) -> None:
    """Queue a speech event (resolves camera name from Protect API)."""
    from app.protect import get_protect_client

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
            (camera.name or f"Unknown ({camera_id})") if camera else f"Unknown ({camera_id})"
        )
    except Exception:
        camera_name = f"Unknown ({camera_id})"

    settings = get_settings()
    language = settings.get("language", "da")
    queue_transcription(event_id, camera_id, camera_name, timestamp_ms, language)
