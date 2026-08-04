from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.models import (
    AvailabilityRequest,
    AvailabilityOption,
    AvailabilityResponse,
    CalendarEventCreateRequest,
    CalendarEventResult,
    ScheduleStatus,
    ScheduleTaskRequest,
    ScheduleTaskResponse,
    VikunjaTask,
)
from app.services.availability import build_availability
from app.services.caldav_client import CalDAVService, CalendarEventMatch


class SchedulingError(RuntimeError):
    pass


class MissingDeadlineError(SchedulingError):
    pass


class NoAvailabilityError(SchedulingError):
    pass


class TaskAlreadyCompletedError(SchedulingError):
    pass


class MultipleTaskEventsError(SchedulingError):
    pass


class CalendarEventCreationError(SchedulingError):
    pass


class SchedulerService:
    def __init__(
        self,
        caldav: CalDAVService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.caldav = caldav or CalDAVService()

    def create_calendar_event(
        self, request: CalendarEventCreateRequest
    ) -> CalendarEventResult:
        calendar_name = (
            request.calendar_name or self.settings.beacon_schedule_calendar
        )
        description = request.description
        if request.source_reference:
            marker = f"Beacon source reference: {request.source_reference}"
            description = f"{description}\n\n{marker}" if description else marker
        try:
            return self.caldav.create_event(
                calendar_name=calendar_name,
                title=request.title,
                description=description,
                start=request.start_iso,
                end=request.end_iso,
            )
        except Exception as exc:
            raise CalendarEventCreationError(str(exc)) from exc

    def _resolve_bounds(
        self,
        task: VikunjaTask,
        request: ScheduleTaskRequest,
    ) -> tuple[datetime, datetime]:
        if task.done:
            raise TaskAlreadyCompletedError(
                f"Task {task.id} is already completed."
            )
        timezone = ZoneInfo(self.settings.beacon_timezone)
        earliest = request.earliest_iso or datetime.now(timezone)
        deadline = request.deadline_iso or task.due_date
        if deadline is None:
            raise MissingDeadlineError(
                "Task has no due date. Supply deadline_iso."
            )
        return earliest, deadline

    def find_slot(
        self,
        task: VikunjaTask,
        request: ScheduleTaskRequest,
        exclude_task_id: int | None = None,
    ) -> AvailabilityResponse:
        earliest, deadline = self._resolve_bounds(task, request)
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
            exclude_task_id=exclude_task_id,
        )
        result = build_availability(availability_request, events)
        if result.no_availability:
            raise NoAvailabilityError("No available work block found.")
        return result

    def schedule_task(
        self,
        task: VikunjaTask,
        request: ScheduleTaskRequest,
    ) -> ScheduleTaskResponse:
        earliest, deadline = self._resolve_bounds(task, request)
        calendar_name = (
            request.calendar_name
            or self.settings.beacon_schedule_calendar
        )
        matches = self.caldav.find_task_events(
            calendar_name=calendar_name,
            task_id=task.id,
            search_start=earliest - timedelta(days=365),
            search_end=deadline + timedelta(days=365),
        )
        if len(matches) > 1:
            raise MultipleTaskEventsError(
                f"Multiple Beacon events found for Vikunja task {task.id}."
            )
        existing = matches[0] if matches else None
        availability = self.find_slot(
            task,
            request,
            exclude_task_id=task.id if existing else None,
        )
        selected = availability.options[0]

        if not request.create_event:
            return self._response(
                ScheduleStatus.RECOMMENDATION_ONLY,
                task,
                selected,
                availability,
                existing,
            )

        if existing is None:
            calendar_event = self.caldav.create_event(
                calendar_name=calendar_name,
                title=f"Work Block — {task.title}",
                description=self._event_description(task),
                start=selected.start_iso,
                end=selected.end_iso,
            )
            return ScheduleTaskResponse(
                status=ScheduleStatus.NEW,
                task=task,
                selected_option=selected,
                calendars_checked=availability.calendars_checked,
                events_found=availability.events_found,
                calendar_event=calendar_event,
                already_scheduled=False,
            )

        if self._same_bounds(existing, selected.start_iso, selected.end_iso):
            return self._response(
                ScheduleStatus.UNCHANGED,
                task,
                selected,
                availability,
                existing,
            )

        updated = self.caldav.update_event(
            match=existing,
            task_id=task.id,
            start=selected.start_iso,
            end=selected.end_iso,
        )
        return ScheduleTaskResponse(
            status=ScheduleStatus.UPDATED,
            task=task,
            selected_option=selected,
            calendars_checked=availability.calendars_checked,
            events_found=availability.events_found,
            calendar_event=updated,
            already_scheduled=True,
        )

    def _same_bounds(
        self,
        existing: CalendarEventMatch,
        start: datetime,
        end: datetime,
    ) -> bool:
        timezone = ZoneInfo(self.settings.beacon_timezone)
        existing_start = existing.result.start_iso.astimezone(timezone)
        existing_end = existing.result.end_iso.astimezone(timezone)
        selected_start = start.astimezone(timezone)
        selected_end = end.astimezone(timezone)
        return (
            existing_start == selected_start
            and existing_end == selected_end
            and existing_end - existing_start == selected_end - selected_start
        )

    @staticmethod
    def _event_description(task: VikunjaTask) -> str:
        return (
            "Scheduled by Beacon\n\n"
            f"Vikunja task ID: {task.id}\n"
            f"Priority: {task.priority}"
        )

    @staticmethod
    def _response(
        status: ScheduleStatus,
        task: VikunjaTask,
        selected: AvailabilityOption,
        availability: AvailabilityResponse,
        existing: CalendarEventMatch | None,
    ) -> ScheduleTaskResponse:
        return ScheduleTaskResponse(
            status=status,
            task=task,
            selected_option=selected,
            calendars_checked=availability.calendars_checked,
            events_found=availability.events_found,
            calendar_event=existing.result if existing else None,
            already_scheduled=existing is not None,
        )
