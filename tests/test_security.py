"""Security regression tests."""

from datetime import UTC, datetime


def test_audio_path_traversal_blocked(client):
    r = client.get("/audio/..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)
    r = client.get("/audio/../etc/passwd")
    assert r.status_code in (400, 404)


def test_database_reset_requires_confirm(client, tmp_db):
    r = client.post("/api/database/reset")
    assert r.status_code == 400
    r = client.post("/api/database/reset", json={"confirm": "no"})
    assert r.status_code == 400
    r = client.post("/api/database/reset", json={"confirm": "yes"})
    assert r.status_code == 200


def test_settings_redacts_sensitive_keys(client, tmp_db):
    # Insert a fake "password" setting and ensure it isn't returned
    import sqlite3

    conn = sqlite3.connect(tmp_db)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('protect_password', 'shh')")
    conn.commit()
    conn.close()

    r = client.get("/api/settings")
    assert r.status_code == 200
    assert "protect_password" not in r.json()["settings"]


def test_csv_export_quotes_formula(client, tmp_db):
    import sqlite3

    conn = sqlite3.connect(tmp_db)
    conn.execute(
        """
        INSERT INTO transcriptions
            (event_id, camera_id, camera_name, timestamp, transcription, status, language)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "e1",
            "c1",
            "Cam",
            datetime.now(tz=UTC).isoformat(),
            "=cmd|',",
            "completed",
            "da",
        ),
    )
    conn.commit()
    conn.close()

    r = client.get("/api/export/csv")
    assert r.status_code == 200
    assert "'=cmd" in r.text  # leading quote prefix added
