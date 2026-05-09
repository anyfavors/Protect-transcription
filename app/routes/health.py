"""Liveness, readiness, and metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.config import WHISPER_URL
from app.database import get_connection
from app.http_clients import get_whisper_client
from app.metrics import render

router = APIRouter()


@router.get("/health")
@router.get("/livez")
async def health_check():
    """Liveness probe — returns 200 as long as the event loop is alive."""
    return {"status": "healthy"}


@router.get("/health/ready")
@router.get("/readyz")
async def readiness_check(response: Response):
    """
    Readiness probe.

    Checks DB write path and speaches reachability. Returns 503 when any
    dependency is unavailable so k8s can pull the pod out of the service.
    Protect connectivity is **not** required for readiness — the app should
    still serve UI/queries even when the NVR is offline.
    """
    checks: dict[str, dict] = {}

    db_ok = True
    try:
        conn = get_connection()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        db_ok = False
        checks["database"] = {"ok": False, "error": str(exc)}
    else:
        checks["database"] = {"ok": True}

    whisper_ok = True
    try:
        client = get_whisper_client()
        r = await client.get(f"{WHISPER_URL}/v1/models", timeout=5.0)
        whisper_ok = r.status_code == 200
        checks["whisper"] = {"ok": whisper_ok, "status_code": r.status_code}
    except Exception as exc:
        whisper_ok = False
        checks["whisper"] = {"ok": False, "error": str(exc)}

    ok = db_ok and whisper_ok
    if not ok:
        response.status_code = 503
    return {"ok": ok, "checks": checks}


@router.get("/metrics")
async def metrics():
    """Prometheus-format metrics."""
    return Response(content=render(), media_type="text/plain; version=0.0.4")
