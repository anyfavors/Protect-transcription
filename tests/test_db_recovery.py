"""Tests for stuck-processing reset and schema migration version."""

import sqlite3
from datetime import UTC, datetime, timedelta


def _insert(db_path, **kwargs):
    defaults = {
        "event_id": "evt",
        "camera_id": "cam",
        "camera_name": "Cam",
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "status": "processing",
    }
    defaults.update(kwargs)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO transcriptions
            (event_id, camera_id, camera_name, timestamp, status, created_at)
        VALUES (:event_id, :camera_id, :camera_name, :timestamp, :status, :created_at)
        """,
        {**defaults, "created_at": defaults.get("created_at")},
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def test_reset_stuck_processing_resets_old(tmp_db):
    from app.database import reset_stuck_processing

    old = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
    fresh = datetime.now(tz=UTC).isoformat()

    _insert(tmp_db, event_id="old", created_at=old)
    _insert(tmp_db, event_id="fresh", created_at=fresh)

    count = reset_stuck_processing(timeout_minutes=10)
    assert count == 1

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT status FROM transcriptions WHERE event_id='old'")
    assert cur.fetchone()[0] == "pending"
    cur.execute("SELECT status FROM transcriptions WHERE event_id='fresh'")
    assert cur.fetchone()[0] == "processing"
    conn.close()


def test_reset_stuck_zero_disables(tmp_db):
    from app.database import reset_stuck_processing

    assert reset_stuck_processing(0) == 0


def test_user_version_set(tmp_db):
    from app.database import SCHEMA_VERSION

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("PRAGMA user_version")
    version = cur.fetchone()[0]
    conn.close()
    assert version == SCHEMA_VERSION
