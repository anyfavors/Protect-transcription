"""Tests for the settings endpoints."""


def test_get_settings_returns_defaults(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert "settings" in data
    assert "available_languages" in data
    assert data["settings"]["language"] == "da"


def test_update_setting(client):
    r = client.put("/api/settings", json={"language": "en"})
    assert r.status_code == 200
    assert r.json()["settings"]["language"] == "en"


def test_update_setting_invalid_buffer(client):
    r = client.put("/api/settings", json={"buffer_before": "999"})
    assert r.status_code == 400


def test_update_setting_unknown_key_ignored(client):
    r = client.put("/api/settings", json={"totally_unknown_key": "value"})
    assert r.status_code == 200
    # Unknown key should not appear in settings
    assert "totally_unknown_key" not in r.json()["settings"]


def test_update_protect_host_strips_protocol(client):
    r = client.put("/api/settings", json={"protect_host": "https://192.168.1.1/"})
    assert r.status_code == 200
    assert r.json()["settings"]["protect_host"] == "192.168.1.1"


def test_vad_filter_normalised_to_string(client):
    r = client.put("/api/settings", json={"vad_filter": True})
    assert r.status_code == 200
    assert r.json()["settings"]["vad_filter"] == "true"

    r2 = client.put("/api/settings", json={"vad_filter": False})
    assert r2.json()["settings"]["vad_filter"] == "false"
