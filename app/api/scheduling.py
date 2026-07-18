from fastapi import APIRouter, Depends, HTTPException

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

router = APIRouter(tags=["scheduling"])


@router.post(
    "/schedule/task/{task_id}",
    response_model=ScheduleTaskResponse,
    dependencies=[Depends(require_api_key)],
)
def schedule_task(
    task_id: int,
    request: ScheduleTaskRequest,
) -> ScheduleTaskResponse:
    try:
        settings = get_settings()

        task = VikunjaClient().get_task(task_id)

        scheduler = SchedulerService()
        availability = scheduler.find_slot(task, request)

        selected_option = availability.options[0]
        calendar_event = None

        if request.create_event:
            calendar_name = (
                request.calendar_name
                or settings.beacon_schedule_calendar
            )

            description = "\n".join(
                [
                    "Scheduled by Beacon",
                    "",
                    f"Vikunja task ID: {task.id}",
                    f"Task priority: {task.priority}",
                    "",
                    task.description,
                ]
            ).strip()

            calendar_event = CalDAVService().create_event(
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
        )

    except VikunjaTaskNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except TaskAlreadyCompletedError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except (MissingDeadlineError, NoAvailabilityError) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except VikunjaError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Scheduling failed: {exc}",
        ) from exc