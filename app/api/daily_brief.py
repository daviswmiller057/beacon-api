from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.models import DailyBriefResponse
from app.security import require_api_key
from app.services.daily_brief import DailyBriefService


router = APIRouter(
    tags=["daily-brief"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/daily", response_model=DailyBriefResponse)
def daily_brief(
    requested_date: date | None = Query(default=None, alias="date"),
) -> DailyBriefResponse:
    try:
        return DailyBriefService().build(requested_date)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Daily brief generation failed: {exc}",
        ) from exc
