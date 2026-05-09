"""Tests for app.util helpers."""

from datetime import UTC, datetime

from app.util import (
    camera_display_name,
    csv_safe,
    find_camera,
    format_srt_time,
    parse_timestamp_to_ms,
    safe_audio_path,
)

# ── format_srt_time ─────────────────────────────────────────────────────────


def test_format_srt_time_zero():
    assert format_srt_time(0) == "00:00:00,000"


def test_format_srt_time_basic():
    assert format_srt_time(65.5) == "00:01:05,500"


def test_format_srt_time_hours():
    assert format_srt_time(3661.123) == "01:01:01,123"


def test_format_srt_time_negative_clamped():
    assert format_srt_time(-1.0) == "00:00:00,000"


# ── parse_timestamp_to_ms ───────────────────────────────────────────────────


def test_parse_timestamp_with_z():
    ms = parse_timestamp_to_ms("2024-01-01T00:00:00Z")
    assert ms == int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)


def test_parse_timestamp_with_offset():
    ms = parse_timestamp_to_ms("2024-01-01T01:00:00+01:00")
    assert ms == int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)


# ── safe_audio_path ─────────────────────────────────────────────────────────


def test_safe_audio_path_valid(tmp_path):
    root = str(tmp_path)
    p = safe_audio_path(root, "20250101_120000_Cam_abcd1234.wav")
    assert p is not None
    assert str(p).startswith(str(tmp_path))


def test_safe_audio_path_traversal_rejected(tmp_path):
    assert safe_audio_path(str(tmp_path), "../etc/passwd") is None
    assert safe_audio_path(str(tmp_path), "..") is None
    assert safe_audio_path(str(tmp_path), "/etc/passwd") is None
    assert safe_audio_path(str(tmp_path), "evil/file.wav") is None
    assert safe_audio_path(str(tmp_path), "") is None


def test_safe_audio_path_special_chars_rejected(tmp_path):
    # Backslashes / null / colons get rejected by the safe-name regex
    assert safe_audio_path(str(tmp_path), "weird\x00name.wav") is None


# ── find_camera + camera_display_name ──────────────────────────────────────


class _FakeCam:
    def __init__(self, cam_id, name, mac):
        self.id = cam_id
        self.name = name
        self.mac = mac


class _FakeBootstrap:
    def __init__(self, cameras):
        self.cameras = {c.id: c for c in cameras}


class _FakeClient:
    def __init__(self, cameras):
        self.bootstrap = _FakeBootstrap(cameras)


def test_find_camera_by_uuid():
    cam = _FakeCam("uuid-1", "Front", "AA:BB:CC:11:22:33")
    client = _FakeClient([cam])
    assert find_camera(client, "uuid-1") is cam


def test_find_camera_by_mac():
    cam = _FakeCam("uuid-1", "Front", "AA:BB:CC:11:22:33")
    client = _FakeClient([cam])
    found = find_camera(client, "aabbcc112233")
    assert found is cam


def test_find_camera_missing():
    client = _FakeClient([])
    assert find_camera(client, "anything") is None


def test_camera_display_name_with_camera():
    cam = _FakeCam("uuid-1", "Hallway", "")
    assert camera_display_name(cam, "uuid-1") == "Hallway"


def test_camera_display_name_missing_camera():
    assert camera_display_name(None, "id-x").startswith("Unknown")


# ── csv_safe ───────────────────────────────────────────────────────────────


def test_csv_safe_passthrough():
    assert csv_safe("hello") == "hello"
    assert csv_safe(None) is None
    assert csv_safe(42) == 42


def test_csv_safe_formula_prefixed():
    assert csv_safe("=1+1") == "'=1+1"
    assert csv_safe("+1") == "'+1"
    assert csv_safe("-cmd") == "'-cmd"
    assert csv_safe("@SUM(A1)") == "'@SUM(A1)"
