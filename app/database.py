"""
SQLite database helpers: schema init, settings CRUD, and connection management.
All connections use WAL mode and a 30-second timeout to avoid lock contention.
"""

import contextlib
import logging
import sqlite3
from pathlib import Path

from app.config import DATABASE_PATH, OLLAMA_MODEL, OLLAMA_URL, PROTECT_HOST

logger = logging.getLogger(__name__)

_DEFAULT_SETTINGS: dict[str, str] = {
    "whisper_model": "Systran/faster-whisper-large-v3",
    "language": "da",
    "buffer_before": "5",
    "buffer_after": "60",
    "vad_filter": "true",
    "beam_size": "5",
    "protect_host": PROTECT_HOST,
    "ollama_url": OLLAMA_URL,
    "ollama_model": OLLAMA_MODEL,
    "condition_on_previous_text": "false",
    "no_speech_threshold": "0.6",
    "compression_ratio_threshold": "2.4",
    # Speaker diarization (requires speaches server support)
    "enable_diarization": "false",
    # Noise / silence pre-filter: minimum RMS energy (0-1 normalised)
    "min_audio_energy": "0.005",
    # Audio compression: compress WAV to Opus/OGG after N days (0 = disabled)
    "audio_compression_days": "7",
}


def get_connection() -> sqlite3.Connection:
    """Open and return a WAL-mode SQLite connection to DATABASE_PATH."""
    conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# Internal alias kept for backward compat within this module
_connect = get_connection


def init_database() -> None:
    """Create tables, indexes, FTS virtual table, and triggers if they don't exist."""
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)

    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transcriptions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id         TEXT UNIQUE,
            camera_id        TEXT,
            camera_name      TEXT,
            timestamp        DATETIME,
            transcription    TEXT,
            segments         TEXT,
            language         TEXT,
            confidence       REAL,
            audio_file       TEXT,
            duration_seconds REAL,
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            status           TEXT DEFAULT 'pending'
        )
    """)

    for index_sql in (
        "CREATE INDEX IF NOT EXISTS idx_timestamp ON transcriptions(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_camera    ON transcriptions(camera_name)",
        "CREATE INDEX IF NOT EXISTS idx_status    ON transcriptions(status)",
    ):
        cur.execute(index_sql)

    cur.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS transcriptions_fts USING fts5(
            transcription,
            camera_name,
            content='transcriptions',
            content_rowid='id'
        )
    """)

    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS transcriptions_ai AFTER INSERT ON transcriptions BEGIN
            INSERT INTO transcriptions_fts(rowid, transcription, camera_name)
            VALUES (new.id, new.transcription, new.camera_name);
        END
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS transcriptions_ad AFTER DELETE ON transcriptions BEGIN
            INSERT INTO transcriptions_fts(transcriptions_fts, rowid, transcription, camera_name)
            VALUES ('delete', old.id, old.transcription, old.camera_name);
        END
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS transcriptions_au AFTER UPDATE ON transcriptions BEGIN
            INSERT INTO transcriptions_fts(transcriptions_fts, rowid, transcription, camera_name)
            VALUES ('delete', old.id, old.transcription, old.camera_name);
            INSERT INTO transcriptions_fts(rowid, transcription, camera_name)
            VALUES (new.id, new.transcription, new.camera_name);
        END
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    for key, value in _DEFAULT_SETTINGS.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS summaries (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            period_type         TEXT NOT NULL,
            period_key          TEXT NOT NULL,
            period_label        TEXT,
            summary             TEXT,
            transcription_count INTEGER DEFAULT 0,
            generated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(period_type, period_key)
        )
    """)

    # Migration: add segments column if missing
    cur.execute("PRAGMA table_info(transcriptions)")
    columns = [col[1] for col in cur.fetchall()]
    if "segments" not in columns:
        logger.info("Migrating database: adding segments column")
        cur.execute("ALTER TABLE transcriptions ADD COLUMN segments TEXT")

    # Rebuild FTS index to cover existing rows (idempotent)
    with contextlib.suppress(sqlite3.OperationalError):
        cur.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')")

    conn.commit()
    conn.close()
    logger.info("Database initialised at %s with FTS5 support", DATABASE_PATH)


def get_settings() -> dict[str, str]:
    """Return all settings as a plain dict."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM settings")
        return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def get_setting(key: str, default: str | None = None) -> str | None:
    """Return a single setting value, or *default* if not found."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def save_setting(key: str, value: str) -> None:
    """Upsert a single setting."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Setting saved: %s = %s", key, value)
