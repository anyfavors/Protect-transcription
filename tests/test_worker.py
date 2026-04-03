"""Tests for the transcription queue helper (queue_transcription)."""

import sqlite3
from datetime import UTC, datetime

from app.worker import queue_transcription


def _now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def test_queue_transcription_inserts_pending(tmp_db):
    result = queue_transcription("evt_001", "cam_abc", "Front Door", _now_ms())
    assert result is True

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT status FROM transcriptions WHERE event_id = 'evt_001'")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "pending"


def test_queue_transcription_duplicate_returns_false(tmp_db):
    ts = _now_ms()
    queue_transcription("evt_dup", "cam_abc", "Front Door", ts)
    result = queue_transcription("evt_dup", "cam_abc", "Front Door", ts)
    assert result is False

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM transcriptions WHERE event_id = 'evt_dup'")
    assert cur.fetchone()[0] == 1
    conn.close()


def test_queue_transcription_stores_language(tmp_db):
    queue_transcription("evt_lang", "cam_abc", "Front Door", _now_ms(), language="en")

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT language FROM transcriptions WHERE event_id = 'evt_lang'")
    row = cur.fetchone()
    conn.close()

    assert row[0] == "en"


def test_queue_multiple_events(tmp_db):
    for i in range(5):
        queue_transcription(f"evt_{i:03d}", "cam_abc", "Front Door", _now_ms() + i)

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM transcriptions WHERE status = 'pending'")
    count = cur.fetchone()[0]
    conn.close()

    assert count == 5
