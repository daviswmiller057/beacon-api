from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.models import TodayDashboardResponse
from app.security import require_api_key
from app.services.dashboard import TodayDashboardService


router = APIRouter(
    tags=["dashboard"],
    dependencies=[Depends(require_api_key)],
)


def dashboard_service_dependency() -> TodayDashboardService:
    return TodayDashboardService()


@router.get("/today", response_model=TodayDashboardResponse)
def today_dashboard(
    service: Annotated[
        TodayDashboardService, Depends(dashboard_service_dependency)
    ],
) -> TodayDashboardResponse:
    try:
        return service.build()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Today dashboard generation failed",
        ) from exc
