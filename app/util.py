"""
Shared helpers used by multiple modules.

Keep this module dependency-light: no FastAPI, no DB, no httpx imports here.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import LOCAL_TZ

# ── SRT formatting ──────────────────────────────────────────────────────────


def format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp (HH:MM:SS,mmm)."""
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# ── Timestamp parsing ───────────────────────────────────────────────────────


def parse_timestamp_to_ms(timestamp_str: str) -> int:
    """
    Parse an ISO-8601 timestamp (with or without tz) to epoch milliseconds.
    Naive timestamps are assumed to be in LOCAL_TZ.
    Raises ValueError on bad input.
    """
    dt = datetime.fromisoformat(str(timestamp_str).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return int(dt.timestamp() * 1000)


# ── Audio filename safety ───────────────────────────────────────────────────

_SAFE_AUDIO_NAME = re.compile(r"^[A-Za-z0-9._\- ]+$")

# Filesystem-unsafe characters across Linux/macOS/Windows.
_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def safe_download_filename(name: str, fallback: str = "file") -> str:
    """
    Sanitize a filename used in Content-Disposition headers (SRT, audio).

    Strips characters that are unsafe on Windows or in HTTP headers, collapses
    whitespace, and falls back to *fallback* if nothing is left.
    """
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", name or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned or fallback


def safe_audio_path(audio_root: str, filename: str) -> Path | None:
    """
    Resolve *filename* within *audio_root*, refusing path traversal.

    Returns the resolved Path on success, or None if the filename is unsafe
    or escapes the audio root.
    """
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return None
    if not _SAFE_AUDIO_NAME.match(filename):
        return None
    root = Path(audio_root).resolve()
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


# ── Camera lookup (UUID or MAC) ─────────────────────────────────────────────


def find_camera(client: Any, camera_id: str) -> Any | None:
    """
    Look up a Protect camera by UUID or normalised MAC address.
    Returns the camera object or None.
    """
    if not camera_id:
        return None
    cam = client.bootstrap.cameras.get(camera_id)
    if cam is not None:
        return cam
    normalized = camera_id.upper().replace(":", "").replace("-", "")
    for c in client.bootstrap.cameras.values():
        if c.mac and c.mac.upper().replace(":", "").replace("-", "") == normalized:
            return c
    return None


def camera_display_name(camera: Any | None, camera_id: str) -> str:
    """Return camera.name or a fallback string when missing."""
    if camera is None:
        return f"Unknown ({camera_id})"
    return camera.name or f"Unknown ({camera_id})"


# ── CSV injection guard ─────────────────────────────────────────────────────

_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> Any:
    """Prefix cells starting with formula triggers with a single quote."""
    if value is None:
        return value
    s = str(value)
    if s and s[0] in _FORMULA_PREFIX:
        return "'" + s
    return value
