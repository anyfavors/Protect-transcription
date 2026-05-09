"""
Sync historical speech events from the UniFi Protect NVR.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_api_token
from app.sync_service import SyncError, run_sync

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/sync", dependencies=[Depends(require_api_token)])
async def sync_speech_events(
    hours: int = Query(default=24, ge=1, le=720),
):
    """Fetch speech events from Protect for the last *hours* hours and queue any missing ones."""
    try:
        return await run_sync(hours)
    except SyncError as exc:
        msg = str(exc)
        if "not configured" in msg:
            raise HTTPException(status_code=400, detail=msg) from exc
        raise HTTPException(status_code=503, detail=msg) from exc
