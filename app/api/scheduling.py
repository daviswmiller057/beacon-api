from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import get_settings
from app.models import ScheduleTaskRequest, ScheduleTaskResponse
from app.security import require_api_key
from app.services.caldav_client import CalDAVService
from app.services.scheduler import (
    MissingDeadlineError,
    NoAvailabilityError,
    SchedulerService,
    TaskAlreadyCompletedError,
)
from app.services.vikunja_client import (
    VikunjaClient,
    VikunjaError,
    VikunjaTaskNotFound,
)


router = APIRouter(
    tags=["scheduling"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/task/{task_id}",
    response_model=ScheduleTaskResponse,
)
def schedule_task(
    task_id: int,
    request: ScheduleTaskRequest,
) -> ScheduleTaskResponse:
    settings = get_settings()
    vikunja = VikunjaClient()
    scheduler = SchedulerService()
    caldav = CalDAVService()

    try:
        task = vikunja.get_task(task_id)
        availability = scheduler.find_slot(task, request)

        if not availability.options:
            raise NoAvailabilityError(
                "No available work block found."
            )

        selected_option = availability.options[0]

        calendar_name = (
            request.calendar_name
            or settings.beacon_schedule_calendar
        )

        existing_event = caldav.find_task_event(
            calendar_name=calendar_name,
            task_id=task.id,
            search_start=(
                selected_option.start_iso - timedelta(days=365)
            ),
            search_end=(
                selected_option.end_iso + timedelta(days=365)
            ),
        )

        if existing_event is not None:
            return ScheduleTaskResponse(
                status="already_scheduled",
                task=task,
                selected_option=selected_option,
                calendars_checked=availability.calendars_checked,
                events_found=availability.events_found,
                calendar_event=existing_event,
                already_scheduled=True,
            )

        calendar_event = None

        if request.create_event:
            description = (
                "Scheduled by Beacon\n\n"
                f"Vikunja task ID: {task.id}\n"
                f"Priority: {task.priority}"
            )

            calendar_event = caldav.create_event(
                calendar_name=calendar_name,
                title=f"Work Block — {task.title}",
                description=description,
                start=selected_option.start_iso,
                end=selected_option.end_iso,
            )

        return ScheduleTaskResponse(
            status=(
                "scheduled"
                if calendar_event is not None
                else "recommended"
            ),
            task=task,
            selected_option=selected_option,
            calendars_checked=availability.calendars_checked,
            events_found=availability.events_found,
            calendar_event=calendar_event,
            already_scheduled=False,
        )

    except VikunjaTaskNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except TaskAlreadyCompletedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except MissingDeadlineError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    except NoAvailabilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    except VikunjaError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Scheduling integration failed: {exc}",
        ) from exc