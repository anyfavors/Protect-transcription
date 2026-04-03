"""
Transcription CRUD routes: list, delete, retry, SRT download, bulk operations.
"""

import contextlib
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse

from app.config import AUDIO_PATH
from app.database import get_connection, get_settings
from app.worker import queue_transcription

logger = logging.getLogger(__name__)
router = APIRouter()


def _format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


@router.get("/api/transcriptions")
async def get_transcriptions(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    camera: str | None = None,
    date: str | None = None,
    search: str | None = None,
    status: str | None = None,
):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        if search:
            search_term = search.replace('"', '""')
            where_clauses, params = [], []
            if camera:
                where_clauses.append("t.camera_name = ?")
                params.append(camera)
            if date:
                where_clauses.append("DATE(t.timestamp) = ?")
                params.append(date)
            if status:
                where_clauses.append("t.status = ?")
                params.append(status)
            extra = (" AND " + " AND ".join(where_clauses)) if where_clauses else ""

            cursor.execute(
                f"SELECT COUNT(*) FROM transcriptions t "
                f"INNER JOIN transcriptions_fts fts ON t.id = fts.rowid "
                f"WHERE transcriptions_fts MATCH ?{extra}",
                [f'"{search_term}"', *params],
            )
            total = cursor.fetchone()[0]

            offset = (page - 1) * per_page
            cursor.execute(
                f"SELECT t.* FROM transcriptions t "
                f"INNER JOIN transcriptions_fts fts ON t.id = fts.rowid "
                f"WHERE transcriptions_fts MATCH ?{extra} "
                f"ORDER BY t.timestamp DESC LIMIT ? OFFSET ?",
                [f'"{search_term}"', *params, per_page, offset],
            )
        else:
            where_clauses, params = [], []
            if camera:
                where_clauses.append("camera_name = ?")
                params.append(camera)
            if date:
                where_clauses.append("DATE(timestamp) = ?")
                params.append(date)
            if status:
                where_clauses.append("status = ?")
                params.append(status)
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            cursor.execute(f"SELECT COUNT(*) FROM transcriptions WHERE {where_sql}", params)
            total = cursor.fetchone()[0]

            offset = (page - 1) * per_page
            cursor.execute(
                f"SELECT * FROM transcriptions WHERE {where_sql} "
                f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                [*params, per_page, offset],
            )

        rows = cursor.fetchall()
        transcriptions = []
        for row in rows:
            segments = None
            try:
                raw = row["segments"]
                if raw:
                    segments = json.loads(raw)
            except (KeyError, json.JSONDecodeError):
                pass

            transcriptions.append(
                {
                    "id": row["id"],
                    "event_id": row["event_id"],
                    "camera_name": row["camera_name"],
                    "timestamp": row["timestamp"],
                    "transcription": row["transcription"],
                    "segments": segments,
                    "language": row["language"],
                    "duration_seconds": row["duration_seconds"],
                    "status": row["status"],
                    "audio_file": row["audio_file"],
                }
            )

        return {
            "transcriptions": transcriptions,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }

    finally:
        conn.close()


@router.get("/api/cameras")
async def get_cameras():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT DISTINCT camera_name FROM transcriptions "
            "WHERE camera_name IS NOT NULL ORDER BY camera_name"
        )
        return {"cameras": [row[0] for row in cursor.fetchall()]}
    finally:
        conn.close()


@router.get("/api/dates")
async def get_dates():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT DISTINCT DATE(timestamp) AS date FROM transcriptions "
            "WHERE timestamp IS NOT NULL ORDER BY date DESC LIMIT 90"
        )
        return {"dates": [row[0] for row in cursor.fetchall()]}
    finally:
        conn.close()


@router.get("/api/stats")
async def get_stats():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM transcriptions")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM transcriptions WHERE status='completed'")
        completed = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM transcriptions WHERE status='processing'")
        processing = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM transcriptions WHERE status='error'")
        errors = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM transcriptions WHERE DATE(timestamp)=DATE('now')")
        today = cursor.fetchone()[0]
        return {
            "total": total,
            "completed": completed,
            "processing": processing,
            "errors": errors,
            "today": today,
        }
    finally:
        conn.close()


@router.delete("/api/transcriptions/{transcription_id}")
async def delete_transcription(transcription_id: int):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM transcriptions WHERE id=?", (transcription_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Transcription not found")

        if row["audio_file"]:
            audio_path = Path(AUDIO_PATH) / row["audio_file"]
            try:
                audio_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Could not delete audio file %s: %s", audio_path, exc)

        cursor.execute("DELETE FROM transcriptions WHERE id=?", (transcription_id,))
        conn.commit()
        return {"status": "deleted", "id": transcription_id}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error deleting transcription %d: %s", transcription_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/api/transcriptions/{transcription_id}/srt")
async def download_srt(transcription_id: int):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM transcriptions WHERE id=?", (transcription_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Transcription not found")

        segments = []
        if row["segments"]:
            with contextlib.suppress(json.JSONDecodeError):
                segments = json.loads(row["segments"])

        if not segments:
            segments = [
                {
                    "start": 0,
                    "end": row["duration_seconds"] or 10,
                    "text": row["transcription"] or "",
                }
            ]

        srt_lines = []
        for i, seg in enumerate(segments, 1):
            text = seg.get("text", "").strip()
            if text:
                srt_lines += [
                    str(i),
                    f"{_format_srt_time(seg.get('start', 0))} --> {_format_srt_time(seg.get('end', seg.get('start', 0) + 5))}",
                    text,
                    "",
                ]

        filename = f"{row['camera_name'] or 'unknown'}_{row['timestamp'] or 'unknown'}.srt".replace(
            " ", "_"
        ).replace(":", "-")
        return PlainTextResponse(
            content="\n".join(srt_lines),
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        conn.close()


@router.post("/api/transcriptions/{transcription_id}/retry")
async def retry_transcription(transcription_id: int):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM transcriptions WHERE id=?", (transcription_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Transcription not found")

        try:
            dt = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            timestamp_ms = int(dt.timestamp() * 1000)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid timestamp: {row['timestamp']}"
            ) from exc

        event_id = row["event_id"]
        camera_id = row["camera_id"]
        camera_name = row["camera_name"]

        cursor.execute("DELETE FROM transcriptions WHERE id=?", (transcription_id,))
        conn.commit()
        conn.close()

        settings = get_settings()
        language = settings.get("language", "da")
        queue_transcription(event_id, camera_id, camera_name, timestamp_ms, language)

        return {"status": "queued", "id": transcription_id, "event_id": event_id}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error retrying transcription %d: %s", transcription_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        with contextlib.suppress(Exception):
            conn.close()


@router.post("/api/transcriptions/retry-errors")
async def retry_all_errors():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM transcriptions WHERE status='error'")
        rows = cursor.fetchall()
        if not rows:
            return {"queued": 0, "message": "No error transcriptions found"}

        settings = get_settings()
        language = settings.get("language", "da")

        to_retry = []
        for row in rows:
            try:
                dt = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
                to_retry.append((dict(row), int(dt.timestamp() * 1000)))
            except Exception as exc:
                logger.error("Failed to parse timestamp for event %s: %s", row["event_id"], exc)

        if not to_retry:
            return {"queued": 0, "message": "All error records had unparseable timestamps"}

        for row, _ in to_retry:
            cursor.execute("DELETE FROM transcriptions WHERE id=?", (row["id"],))
        conn.commit()
        conn.close()

        queued = sum(
            queue_transcription(row["event_id"], row["camera_id"], row["camera_name"], ts, language)
            for row, ts in to_retry
        )
        return {"queued": queued, "message": f"Queued {queued} transcriptions for retry"}

    except Exception as exc:
        logger.exception("Error retrying all errors: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        with contextlib.suppress(Exception):
            conn.close()


@router.post("/api/transcriptions/retranscribe-all")
async def retranscribe_all(request: Request):
    request_body = await request.json()
    include_errors: bool = request_body.get("include_errors", True)
    statuses = ("'completed'", "'error'") if include_errors else ("'completed'",)
    status_clause = f"status IN ({', '.join(statuses)})"

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            UPDATE transcriptions
            SET status='pending',
                transcription=NULL, segments=NULL, language=NULL,
                confidence=NULL, duration_seconds=NULL
            WHERE {status_clause}
        """)
        count = cursor.rowcount
        conn.commit()
    finally:
        conn.close()

    return {"reset": count, "message": f"Queued {count} transcriptions for re-processing"}


@router.get("/audio/{filename}")
async def get_audio(filename: str):
    file_path = Path(AUDIO_PATH) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(file_path, media_type="audio/wav", filename=filename)


@router.post("/api/database/reset")
async def reset_database():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM transcriptions")
        transcription_count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM transcriptions")
        conn.commit()
        cursor.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()

    audio_files_deleted = 0
    audio_dir = Path(AUDIO_PATH)
    if audio_dir.exists():
        for f in audio_dir.glob("*.wav"):
            try:
                f.unlink()
                audio_files_deleted += 1
            except Exception as exc:
                logger.warning("Failed to delete audio file %s: %s", f, exc)

    return {
        "status": "success",
        "message": "Database reset successfully",
        "transcriptions_deleted": transcription_count,
        "audio_files_deleted": audio_files_deleted,
    }
