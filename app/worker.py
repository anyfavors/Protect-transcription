"""
Background transcription worker, audio compression worker, and queue helpers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path

from app.config import AUDIO_PATH, DATABASE_PATH, LOCAL_TZ
from app.database import get_settings, reset_stuck_processing
from app.metrics import inc, set_gauge
from app.transcription import (
    compute_audio_rms,
    fetch_audio_clip,
    save_audio_file,
    transcribe_audio,
)

logger = logging.getLogger(__name__)


def _open_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


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
    conn = _open_conn()
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
        inc("events_queued_total")
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

    settings = get_settings()
    buffer_before = int(settings.get("buffer_before", "5"))
    buffer_after = int(settings.get("buffer_after", "60"))

    try:
        event_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=LOCAL_TZ)
    except Exception as exc:
        _mark_error(record_id, f"Invalid timestamp: {timestamp_str} ({exc})")
        return

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
    existing_audio_file: str | None = row.get("audio_file")
    cached_path = Path(AUDIO_PATH) / existing_audio_file if existing_audio_file else None

    audio_data: bytes | None = None
    audio_filename: str | None = None
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
            _mark_error(record_id, "Failed to fetch audio")
            await _broadcast_update(record_id, "error", camera_name, timestamp_str)
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
            inc("transcriptions_filtered_total")
            _update_filtered(
                record_id,
                f"Silence/noise detected (RMS energy {rms:.5f} below threshold {min_energy})",
                audio_filename or "",
                duration,
            )
            await _broadcast_update(record_id, "filtered", camera_name, timestamp_str)
            return

    result = await transcribe_audio(audio_data)
    duration = len(audio_data) / (16000 * 2)  # 16 kHz, 16-bit

    if "error" in result:
        _update_error_with_audio(
            record_id, f"Transcription error: {result['error']}", audio_filename
        )
        final_status = "error"
    elif result.get("empty"):
        inc("transcriptions_filtered_total")
        _update_filtered(
            record_id,
            "Empty transcription (no speech detected by Whisper)",
            audio_filename or "",
            duration,
        )
        final_status = "filtered"
    else:
        segments = result.get("segments", [])
        segments_json = json.dumps(segments) if segments else None
        _update_completed(
            record_id,
            result.get("text", ""),
            segments_json,
            result.get("language", "da"),
            result.get("confidence", 0),
            audio_filename or "",
            duration,
        )
        final_status = "completed"

    logger.info("Completed event %s from %s", event_id, camera_name)
    await _broadcast_update(record_id, final_status, camera_name, timestamp_str)


def _mark_error(record_id: int, message: str) -> None:
    conn = _open_conn()
    try:
        conn.execute(
            "UPDATE transcriptions SET status='error', transcription=? WHERE id=?",
            (message[:500], record_id),
        )
        conn.commit()
    finally:
        conn.close()


def _update_error_with_audio(record_id: int, message: str, audio_filename: str | None) -> None:
    conn = _open_conn()
    try:
        conn.execute(
            "UPDATE transcriptions SET status='error', transcription=?, audio_file=? WHERE id=?",
            (message[:1000], audio_filename, record_id),
        )
        conn.commit()
    finally:
        conn.close()


def _update_filtered(record_id: int, message: str, audio_filename: str, duration: float) -> None:
    conn = _open_conn()
    try:
        conn.execute(
            """
            UPDATE transcriptions
            SET status='filtered',
                transcription=?,
                audio_file=?,
                duration_seconds=?
            WHERE id=?
            """,
            (message, audio_filename, duration, record_id),
        )
        conn.commit()
    finally:
        conn.close()


def _update_completed(
    record_id: int,
    text: str,
    segments_json: str | None,
    language: str,
    confidence: float,
    audio_filename: str,
    duration: float,
) -> None:
    conn = _open_conn()
    try:
        conn.execute(
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
            (text, segments_json, language, confidence, audio_filename, duration, record_id),
        )
        conn.commit()
    finally:
        conn.close()


async def _claim_next_pending() -> dict | None:
    """Atomically claim the next pending row and return it as a dict."""
    conn = _open_conn()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT id, event_id, camera_id, camera_name, timestamp, language, audio_file
            FROM transcriptions
            WHERE status = 'pending'
            ORDER BY timestamp ASC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None
        cur.execute(
            "UPDATE transcriptions SET status='processing' WHERE id=? AND status='pending'",
            (row["id"],),
        )
        if cur.rowcount == 0:
            conn.commit()
            return None
        conn.commit()
        return dict(row)
    finally:
        conn.close()


async def transcription_worker() -> None:
    """Infinite loop: atomically claim one pending row and process it."""
    while True:
        try:
            row = await _claim_next_pending()
            if row:
                await process_pending_transcription(row)
                set_gauge("queue_pending_rows", _count_pending())
                await asyncio.sleep(1)
            else:
                set_gauge("queue_pending_rows", 0)
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Error in transcription worker: %s", exc)
            inc("worker_loop_errors_total")
            await asyncio.sleep(10)


def _count_pending() -> int:
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transcriptions WHERE status='pending'")
        return int(cur.fetchone()[0])
    finally:
        conn.close()


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


# ---------------------------------------------------------------------------
# Periodic workers
# ---------------------------------------------------------------------------
async def audio_compression_worker() -> None:
    """
    Periodically compress old WAV audio files to Opus/OGG.

    Runs hourly. Controlled by the ``audio_compression_days`` setting:
    WAV files referenced by transcriptions older than N days are converted
    to OGG (libopus @ 32 kbps), the original is deleted, and the DB is updated.
    Set to 0 to disable.
    """
    while True:
        try:
            settings = get_settings()
            days = int(settings.get("audio_compression_days", "7"))
            if days > 0:
                await _run_audio_compression(days)
            await asyncio.sleep(3600)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Audio compression worker error: %s", exc)
            await asyncio.sleep(60)


async def _run_audio_compression(days: int) -> None:
    """Single compression pass — extracted so it's testable and re-runnable."""
    conn = _open_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, audio_file FROM transcriptions "
            "WHERE audio_file LIKE '%.wav' AND timestamp < datetime('now', ?)",
            (f"-{days} days",),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return

    compressed = 0
    for row_id, audio_file in rows:
        wav_path = Path(AUDIO_PATH) / audio_file
        if not wav_path.exists():
            continue

        ogg_name = audio_file.rsplit(".", 1)[0] + ".ogg"
        ogg_path = Path(AUDIO_PATH) / ogg_name

        try:
            proc = await asyncio.create_subprocess_exec(
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
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, _stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                rc = proc.returncode
            except TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("ffmpeg timeout compressing %s", audio_file)
                ogg_path.unlink(missing_ok=True)
                continue
        except FileNotFoundError:
            logger.error("ffmpeg not found on PATH; cannot compress audio")
            return

        if rc == 0 and ogg_path.exists() and ogg_path.stat().st_size > 0:
            wav_path.unlink(missing_ok=True)
            conn = _open_conn()
            try:
                conn.execute(
                    "UPDATE transcriptions SET audio_file=? WHERE id=?",
                    (ogg_name, row_id),
                )
                conn.commit()
            finally:
                conn.close()
            compressed += 1
        else:
            ogg_path.unlink(missing_ok=True)
            logger.warning("Failed to compress %s (rc=%s)", audio_file, rc)

    if compressed:
        inc("audio_files_compressed_total", compressed)
        logger.info("Compressed %d audio files to Opus/OGG", compressed)


async def auto_sync_worker() -> None:
    """
    Periodically pull recent speech events from the Protect NVR.
    Webhook delivery is the realtime path; this is the safety net.

    Cadence and lookback are read live from settings (auto_sync_interval_minutes,
    auto_sync_hours), so changes take effect on the next iteration.
    """
    await asyncio.sleep(60)  # Let Protect client init first

    while True:
        try:
            from app.sync_service import SyncError, run_sync

            settings = get_settings()
            try:
                hours = int(settings.get("auto_sync_hours", "24"))
            except ValueError:
                hours = 24
            try:
                interval_minutes = int(settings.get("auto_sync_interval_minutes", "60"))
            except ValueError:
                interval_minutes = 60
            interval_minutes = max(5, interval_minutes)

            try:
                result = await run_sync(hours=hours)
                inc("auto_sync_runs_total")
                logger.info(
                    "Auto-sync: queued=%d skipped=%d live_skipped=%d",
                    result.get("events_queued", 0),
                    result.get("events_skipped", 0),
                    result.get("events_live_skipped", 0),
                )
            except SyncError as exc:
                logger.warning("Auto-sync skipped: %s", exc)
            except Exception as exc:
                inc("auto_sync_errors_total")
                logger.exception("Auto-sync error: %s", exc)

            await asyncio.sleep(interval_minutes * 60)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Auto-sync worker loop error: %s", exc)
            await asyncio.sleep(300)


def _seconds_until_next(target: time) -> float:
    """Seconds from now until the next occurrence of *target* in LOCAL_TZ."""
    now = datetime.now(tz=LOCAL_TZ)
    today_target = now.replace(
        hour=target.hour, minute=target.minute, second=target.second, microsecond=0
    )
    if today_target <= now:
        today_target += timedelta(days=1)
    return (today_target - now).total_seconds()


def _parse_hhmm(value: str, default: time) -> time:
    try:
        h, m = value.split(":", 1)
        return time(hour=int(h), minute=int(m))
    except Exception:
        return default


async def _generate_daily_summary_safe(date_key: str) -> bool:
    """Generate the daily summary for *date_key*; swallow 404 (no transcripts)."""
    from app.summaries import generate_summary

    try:
        await generate_summary("daily", date_key)
        inc("summaries_generated_total")
        return True
    except Exception as exc:  # 404 / Ollama down / etc
        logger.info("Auto-summary skipped for %s: %s", date_key, exc)
        return False


async def _summary_catchup() -> None:
    """
    On startup, generate any missing daily summaries from the last 7 days.

    Skips days where a summary already exists with a matching transcription
    count (i.e. not stale).
    """
    from app.summaries import get_summaries

    try:
        existing = {item["period_key"]: item for item in get_summaries("daily").get("items", [])}
    except Exception as exc:
        logger.warning("Summary catch-up: failed to read existing summaries: %s", exc)
        return

    today = datetime.now(tz=LOCAL_TZ).date()
    for delta in range(1, 8):  # yesterday .. 7 days ago
        day = today - timedelta(days=delta)
        key = day.strftime("%Y-%m-%d")
        item = existing.get(key)
        if item and item.get("summary") and not item.get("stale"):
            continue
        if not item:
            continue  # No transcripts for that day, skip
        logger.info("Summary catch-up: generating missing daily summary for %s", key)
        await _generate_daily_summary_safe(key)


async def auto_summary_worker() -> None:
    """Generate the daily summary every night at the configured local time."""
    default_target = time(hour=23, minute=55)

    # Catch up missing summaries from the last week (e.g. pod was offline at 23:55).
    try:
        await _summary_catchup()
    except Exception as exc:
        logger.exception("Summary catch-up failed: %s", exc)

    while True:
        try:
            settings = get_settings()
            target = _parse_hhmm(settings.get("auto_summary_time", "23:55"), default_target)

            delay = _seconds_until_next(target)
            logger.info(
                "Auto-summary sleeping %.0fs until %s local", delay, target.strftime("%H:%M")
            )
            await asyncio.sleep(delay)

            today_key = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d")
            if await _generate_daily_summary_safe(today_key):
                logger.info("Auto-summary generated for %s", today_key)

            # Avoid tight loop if clock jumps backwards.
            await asyncio.sleep(60)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Auto-summary worker loop error: %s", exc)
            await asyncio.sleep(300)


async def stuck_processing_recovery_worker() -> None:
    """
    Periodically reclaim rows stuck in 'processing'.

    This handles two failure modes:
    * Pod was killed mid-transcription (SIGTERM during process_pending_transcription).
    * Worker process died but the row was never reset.

    Runs every 5 minutes, controlled by `processing_timeout_minutes` setting.
    """
    while True:
        try:
            settings = get_settings()
            try:
                timeout_min = int(settings.get("processing_timeout_minutes", "10"))
            except ValueError:
                timeout_min = 10
            count = reset_stuck_processing(timeout_min)
            if count:
                inc("stuck_processing_resets_total", count)
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Stuck-processing recovery error: %s", exc)
            await asyncio.sleep(300)


async def protect_health_worker() -> None:
    """
    Periodically refresh the Protect bootstrap so dead TCP connections are
    detected proactively (instead of waiting for the next webhook/sync to fail).
    """
    while True:
        try:
            await asyncio.sleep(300)
            from app.protect import get_protect_client

            try:
                client = await get_protect_client()
                await client.update()
                set_gauge("protect_connected", 1)
            except Exception as exc:
                set_gauge("protect_connected", 0)
                logger.warning("Protect health check failed: %s", exc)
                inc("protect_health_failures_total")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Protect health worker error: %s", exc)
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
    from app.util import camera_display_name, find_camera

    try:
        client = await get_protect_client()
        camera = find_camera(client, camera_id)
        camera_name = camera_display_name(camera, camera_id)
    except Exception:
        camera_name = f"Unknown ({camera_id})"

    settings = get_settings()
    language = settings.get("language", "da")
    queue_transcription(event_id, camera_id, camera_name, timestamp_ms, language)
