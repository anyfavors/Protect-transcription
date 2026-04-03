"""
AI summary generation via Ollama.
"""

import logging
import sqlite3
from datetime import datetime

import httpx
from fastapi import HTTPException

from app.config import LOCAL_TZ, OLLAMA_MODEL, OLLAMA_URL
from app.database import get_connection, get_settings

logger = logging.getLogger(__name__)

_PERIOD_EXPR = {
    "daily": "strftime('%Y-%m-%d', timestamp)",
    "weekly": "strftime('%Y-W%W', timestamp)",
    "monthly": "strftime('%Y-%m', timestamp)",
}


def _period_expr(period: str) -> str:
    if period not in _PERIOD_EXPR:
        raise ValueError(f"period must be one of {list(_PERIOD_EXPR)}")
    return _PERIOD_EXPR[period]


def get_summaries(period: str) -> dict:
    """
    Return stored summaries and periods with transcriptions for the given period type.
    """
    expr = _period_expr(period)

    conn = get_connection()

    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(f"""
        SELECT {expr} AS period_key, COUNT(*) AS cnt
        FROM transcriptions
        WHERE status = 'completed' AND transcription IS NOT NULL AND transcription != ''
        GROUP BY period_key
        ORDER BY period_key DESC
        LIMIT 60
    """)
    periods = [{"period_key": row["period_key"], "count": row["cnt"]} for row in cursor.fetchall()]

    cursor.execute(
        "SELECT period_key, summary, transcription_count, generated_at "
        "FROM summaries WHERE period_type = ? ORDER BY period_key DESC",
        (period,),
    )
    summaries_map = {row["period_key"]: dict(row) for row in cursor.fetchall()}
    conn.close()

    result = []
    for p in periods:
        key = p["period_key"]
        s = summaries_map.get(key)
        result.append(
            {
                "period_key": key,
                "count": p["count"],
                "summary": s["summary"] if s else None,
                "generated_at": s["generated_at"] if s else None,
                "stale": s is not None and s["transcription_count"] != p["count"],
            }
        )

    return {"period": period, "items": result}


async def generate_summary(period: str, period_key: str) -> dict:
    """
    Generate (or regenerate) an AI summary for the given period via Ollama.
    Raises HTTPException on bad input or upstream failure.
    """
    _period_expr(period)  # validates

    date_filter = f"{_PERIOD_EXPR[period]} = ?"

    conn = get_connection()

    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        f"""
        SELECT camera_name, timestamp, transcription
        FROM transcriptions
        WHERE status = 'completed' AND transcription IS NOT NULL AND transcription != ''
          AND {date_filter}
        ORDER BY timestamp ASC
        """,
        (period_key,),
    )
    rows = cursor.fetchall()

    if not rows:
        conn.close()
        raise HTTPException(status_code=404, detail="No transcriptions found for this period")

    lines = []
    for row in rows:
        try:
            dt = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            ts = dt.astimezone(LOCAL_TZ).strftime("%H:%M")
        except Exception:
            ts = str(row["timestamp"])
        lines.append(f"[{ts}] {row['camera_name']}: {row['transcription']}")

    transcript_block = "\n".join(lines)
    count = len(rows)

    period_label = f"week {period_key}" if period == "weekly" else period_key

    settings = get_settings()
    ollama_url = settings.get("ollama_url", OLLAMA_URL).rstrip("/")
    ollama_model = settings.get("ollama_model", OLLAMA_MODEL)

    system_prompt = (
        "You are a helpful home assistant summarising audio transcriptions captured by security cameras. "
        "The transcriptions may be in Danish, English, or a mix. "
        "Write your summary in the same language as the majority of the transcriptions. "
        "Be concise. Group related events. Note who spoke (by camera location) and any notable topics or visitors."
    )
    user_prompt = (
        f"Here are the transcriptions captured during {period_label}:\n\n"
        f"{transcript_block}\n\n"
        "Please write a clear, concise summary of what happened, organised by theme or time of day."
    )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{ollama_url}/v1/chat/completions",
                json={
                    "model": ollama_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                },
            )
            if response.status_code != 200:
                conn.close()
                raise HTTPException(
                    status_code=502,
                    detail=f"Ollama error {response.status_code}: {response.text[:300]}",
                )
            summary_text = response.json()["choices"][0]["message"]["content"].strip()

    except HTTPException:
        conn.close()
        raise
    except Exception as exc:
        conn.close()
        logger.exception("Error calling Ollama: %s", exc)
        raise HTTPException(status_code=502, detail=f"Failed to reach Ollama: {exc}") from exc

    cursor.execute(
        """
        INSERT INTO summaries
            (period_type, period_key, period_label, summary, transcription_count, generated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(period_type, period_key) DO UPDATE SET
            summary             = excluded.summary,
            transcription_count = excluded.transcription_count,
            period_label        = excluded.period_label,
            generated_at        = CURRENT_TIMESTAMP
        """,
        (period, period_key, period_label, summary_text, count),
    )
    conn.commit()
    conn.close()

    logger.info("Generated %s summary for %s (%d transcriptions)", period, period_key, count)
    return {
        "period_type": period,
        "period_key": period_key,
        "summary": summary_text,
        "transcription_count": count,
    }
