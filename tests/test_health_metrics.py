"""Tests for /metrics and /health endpoints."""


def test_metrics_endpoint(client):
    from app.metrics import inc

    inc("test_counter_total", 3)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "test_counter_total" in r.text
    assert "TYPE" in r.text


def test_health_alive(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy"}


def test_readiness_db_ok_whisper_down(client):
    """Whisper unreachable in tests — readiness should return 503."""
    r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["whisper"]["ok"] is False
