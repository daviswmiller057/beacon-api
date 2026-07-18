from fastapi import APIRouter, Depends, HTTPException, status

from app.models import ScheduleTaskRequest, ScheduleTaskResponse
from app.security import require_api_key
from app.services.caldav_client import (
    CalDAVError,
    CalendarEventNotFoundError,
    CalendarEventUpdateError,
)
from app.services.scheduler import (
    MissingDeadlineError,
    MultipleTaskEventsError,
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
    try:
        task = VikunjaClient().get_task(task_id)
        return SchedulerService().schedule_task(task, request)
    except VikunjaTaskNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except CalendarEventNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except TaskAlreadyCompletedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except MultipleTaskEventsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except MissingDeadlineError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except NoAvailabilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except VikunjaError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except CalendarEventUpdateError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except CalDAVError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Scheduling integration failed: {exc}",
        ) from exc
