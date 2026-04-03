from fastapi import APIRouter, HTTPException, Query, Request

from app import summaries as summary_service

router = APIRouter()


@router.get("/api/summaries")
async def get_summaries(period: str = Query("daily")):
    if period not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="period must be daily, weekly, or monthly")
    return summary_service.get_summaries(period)


@router.post("/api/summaries/generate")
async def generate_summary(request: Request):
    data = await request.json()
    period = data.get("period_type", "daily")
    period_key = data.get("period_key", "")

    if period not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="period_type must be daily, weekly, or monthly")
    if not period_key:
        raise HTTPException(status_code=400, detail="period_key is required")

    return await summary_service.generate_summary(period, period_key)
