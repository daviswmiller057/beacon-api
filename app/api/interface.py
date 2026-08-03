from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import get_settings
from app.intake.interpreter import InterpreterConfigurationError, InterpreterError
from app.models import (
    DailyBriefResponse,
    InteractRequest,
    InteractResponse,
    ServiceStatusResponse,
)
from app.security import require_api_key
from app.services.caldav_client import CalDAVError, CalendarEventNotFoundError
from app.services.daily_brief import DailyBriefService
from app.services.interaction import (
    AmbiguousTaskError,
    InteractionService,
    InteractionTaskNotFound,
    UnsupportedIntentError,
)
from app.services.scheduler import (
    MissingDeadlineError,
    MultipleTaskEventsError,
    NoAvailabilityError,
    TaskAlreadyCompletedError,
)
from app.services.vikunja_client import VikunjaError, VikunjaTaskNotFound
from app.version import VERSION


router = APIRouter(
    tags=["interaction"],
    dependencies=[Depends(require_api_key)],
)


@router.post("/interact", response_model=InteractResponse)
def interact(request: InteractRequest) -> InteractResponse:
    try:
        return InteractionService().interact(request)
    except UnsupportedIntentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except (InteractionTaskNotFound, VikunjaTaskNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (
        AmbiguousTaskError,
        MultipleTaskEventsError,
        NoAvailabilityError,
        TaskAlreadyCompletedError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except CalendarEventNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (MissingDeadlineError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except (VikunjaError, CalDAVError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except InterpreterConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except InterpreterError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Interaction failed: {exc}",
        ) from exc


@router.get("/brief", response_model=DailyBriefResponse, tags=["daily-brief"])
def brief(
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


@router.get("/status", response_model=ServiceStatusResponse, tags=["system"])
def service_status() -> ServiceStatusResponse:
    settings = get_settings()
    return ServiceStatusResponse(
        status="ok",
        service="beacon-api",
        version=VERSION,
        timezone=settings.beacon_timezone,
        calendars=settings.calendar_names,
        schedule_calendar=settings.beacon_schedule_calendar,
        integrations={
            "nextcloud": True,
            "vikunja": True,
            "home_assistant": bool(
                settings.home_assistant_url and settings.home_assistant_token
            ),
            "travel": settings.daily_brief_travel_enabled,
        },
        interaction_modes=["natural_language", "structured_intent"],
    )
