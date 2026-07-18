from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models import (
    AvailabilityRequest,
    AvailabilityResponse,
    ScheduleTaskRequest,
    VikunjaTask,
)
from app.services.availability import build_availability
from app.services.caldav_client import CalDAVService


class SchedulingError(RuntimeError):
    pass


class MissingDeadlineError(SchedulingError):
    pass


class NoAvailabilityError(SchedulingError):
    pass


class TaskAlreadyCompletedError(SchedulingError):
    pass


class SchedulerService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.caldav = CalDAVService()

    def find_slot(
        self,
        task: VikunjaTask,
        request: ScheduleTaskRequest,
    ) -> AvailabilityResponse:
        if task.done:
            raise TaskAlreadyCompletedError(
                f"Task {task.id} is already completed."
            )

        timezone = ZoneInfo(self.settings.beacon_timezone)

        now = datetime.now(timezone)

        earliest = request.earliest_iso or now
        deadline = request.deadline_iso or task.due_date

        if deadline is None:
            raise MissingDeadlineError(
                "Task has no due date. Supply deadline_iso."
            )

        availability_request = AvailabilityRequest(
            earliest_iso=earliest,
            deadline_iso=deadline,
            duration_minutes=request.duration_minutes,
            buffer_before_minutes=request.buffer_before_minutes,
            buffer_after_minutes=request.buffer_after_minutes,
            calendar_names=request.availability_calendars,
            daily_start=request.daily_start,
            daily_end=request.daily_end,
            max_options=10,
        )

        events = self.caldav.fetch_busy_intervals(
            start=availability_request.earliest_iso,
            end=availability_request.deadline_iso,
            calendar_names=availability_request.calendar_names,
        )

        result = build_availability(
            availability_request,
            events,
        )

        if result.no_availability:
            raise NoAvailabilityError(
                "No available work block found."
            )

        return result