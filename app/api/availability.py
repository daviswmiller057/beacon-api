from fastapi import APIRouter, Depends, HTTPException

from app.models import AvailabilityRequest, AvailabilityResponse
from app.security import require_api_key
from app.services.availability import build_availability
from app.services.caldav_client import CalDAVService

router = APIRouter(tags=["availability"])


@router.post(
    "/availability",
    response_model=AvailabilityResponse,
    dependencies=[Depends(require_api_key)],
)
def availability(request: AvailabilityRequest) -> AvailabilityResponse:
    try:
        service = CalDAVService()
        events = service.fetch_busy_intervals(
            start=request.earliest_iso,
            end=request.deadline_iso,
            calendar_names=request.calendar_names,
        )
        return build_availability(request, events)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Calendar lookup failed: {exc}") from exc
