"""
Export endpoints: CSV, JSON, and bulk SRT (ZIP) downloads.
"""

import contextlib
import csv
import io
import json
import logging
import sqlite3
import zipfile
from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse, Response

from app.config import LOCAL_TZ
from app.database import get_connection

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_export_query(
    cursor: sqlite3.Cursor,
    camera: str | None,
    date_from: str | None,
    date_to: str | None,
    status: str | None,
    search: str | None,
) -> list[sqlite3.Row]:
    """Build and execute the export query with optional filters."""
    where = ["1=1"]
    params: list = []

    if camera:
        where.append("camera_name = ?")
        params.append(camera)
    if date_from:
        where.append("DATE(timestamp) >= ?")
        params.append(date_from)
    if date_to:
        where.append("DATE(timestamp) <= ?")
        params.append(date_to)
    if status:
        where.append("status = ?")
        params.append(status)
    if search:
        where.append(
            "id IN (SELECT rowid FROM transcriptions_fts WHERE transcriptions_fts MATCH ?)"
        )
        params.append(f'"{search.replace(chr(34), chr(34) + chr(34))}"')

    sql = f"SELECT * FROM transcriptions WHERE {' AND '.join(where)} ORDER BY timestamp DESC"
    cursor.execute(sql, params)
    return cursor.fetchall()


def _format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _generate_srt(row: sqlite3.Row) -> str:
    """Generate SRT content for a single transcription row."""
    segments = []
    if row["segments"]:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            segments = json.loads(row["segments"])

    if not segments:
        segments = [
            {
                "start": 0,
                "end": row["duration_seconds"] or 10,
                "text": row["transcription"] or "",
            }
        ]

    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        text = seg.get("text", "").strip()
        if not text:
            continue
        speaker = seg.get("speaker", "")
        prefix = f"[{speaker}] " if speaker else ""
        start = _format_srt_time(seg.get("start", 0))
        end = _format_srt_time(seg.get("end", seg.get("start", 0) + 5))
        lines.extend([str(i), f"{start} --> {end}", f"{prefix}{text}", ""])

    return "\n".join(lines)


@router.get("/api/export/csv")
async def export_csv(
    camera: str | None = None,
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    status: str | None = None,
    search: str | None = None,
):
    """Export transcriptions as CSV."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        rows = _build_export_query(cursor, camera, date_from, date_to, status, search)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "event_id",
                "camera_name",
                "timestamp",
                "status",
                "language",
                "confidence",
                "duration_seconds",
                "transcription",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["event_id"],
                    row["camera_name"],
                    row["timestamp"],
                    row["status"],
                    row["language"],
                    row["confidence"],
                    row["duration_seconds"],
                    row["transcription"],
                ]
            )

        now = datetime.now(tz=LOCAL_TZ).strftime("%Y%m%d_%H%M%S")
        return PlainTextResponse(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="transcriptions_{now}.csv"'},
        )
    finally:
        conn.close()


@router.get("/api/export/json")
async def export_json(
    camera: str | None = None,
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    status: str | None = None,
    search: str | None = None,
):
    """Export transcriptions as JSON."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        rows = _build_export_query(cursor, camera, date_from, date_to, status, search)

        items = []
        for row in rows:
            segments = None
            if row["segments"]:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    segments = json.loads(row["segments"])
            items.append(
                {
                    "id": row["id"],
                    "event_id": row["event_id"],
                    "camera_name": row["camera_name"],
                    "timestamp": row["timestamp"],
                    "status": row["status"],
                    "language": row["language"],
                    "confidence": row["confidence"],
                    "duration_seconds": row["duration_seconds"],
                    "transcription": row["transcription"],
                    "segments": segments,
                }
            )

        now = datetime.now(tz=LOCAL_TZ).strftime("%Y%m%d_%H%M%S")
        return Response(
            content=json.dumps(items, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="transcriptions_{now}.json"'},
        )
    finally:
        conn.close()


@router.get("/api/export/srt")
async def export_srt_zip(
    camera: str | None = None,
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    search: str | None = None,
):
    """Export all completed transcriptions as a ZIP of SRT files."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        rows = _build_export_query(cursor, camera, date_from, date_to, "completed", search)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for row in rows:
                srt = _generate_srt(row)
                if not srt.strip():
                    continue
                ts = (row["timestamp"] or "unknown").replace(":", "-").replace(" ", "_")
                cam = (row["camera_name"] or "unknown").replace(" ", "_")
                zf.writestr(f"{cam}_{ts}.srt", srt)

        now = datetime.now(tz=LOCAL_TZ).strftime("%Y%m%d_%H%M%S")
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="transcriptions_{now}.zip"'},
        )
    finally:
        conn.close()
