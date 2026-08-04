import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.intake.errors import (
    AmbiguousTaskError,
    InteractionError,
    InteractionTaskNotFound,
)
from app.models import (
    ActionPlan,
    ActionType,
    CalendarEventCreateStatus,
    CreateCalendarEventResponse,
    DailyBriefResponse,
    InteractResponse,
    InteractionAction,
    PlannedAction,
    ScheduleTaskRequest,
    ScheduleTaskResponse,
    VikunjaTask,
)
from app.services.daily_brief import DailyBriefService
from app.services.calendar_events import CalendarEventService
from app.services.scheduler import SchedulerService
from app.services.vikunja_client import VikunjaClient


class ActionExecutor:
    """Execute only operations already authorized by an ActionPlan."""

    def __init__(
        self,
        *,
        vikunja: VikunjaClient,
        scheduler: SchedulerService,
        daily_brief: DailyBriefService,
        calendar_events: CalendarEventService | None = None,
    ) -> None:
        self.vikunja = vikunja
        self.scheduler = scheduler
        self.daily_brief = daily_brief
        self.calendar_events = calendar_events or CalendarEventService()

    def execute(
        self,
        plan: ActionPlan,
        now: datetime,
        timezone: ZoneInfo,
    ) -> InteractResponse:
        actions_taken: list[InteractionAction] = []
        current_task: VikunjaTask | None = None
        brief: DailyBriefResponse | None = None
        scheduled: ScheduleTaskResponse | None = None
        fixed_event: CreateCalendarEventResponse | None = None

        for action in plan.actions:
            if action.action is ActionType.REQUEST_CLARIFICATION:
                question = action.question or "Could you clarify what you want Beacon to do?"
                return InteractResponse(
                    result=question,
                    intent=plan.intent,
                    plan=plan,
                    actions_taken=[
                        InteractionAction(
                            action="clarification_requested", status="PENDING"
                        )
                    ],
                )
            if action.action is ActionType.GENERATE_BRIEF:
                brief = self.daily_brief.build(action.deadline)
                actions_taken.append(self._brief_audit(brief))
                continue
            if action.action is ActionType.CREATE_CALENDAR_EVENT:
                fixed_event = self.calendar_events.create_fixed_event(
                    title=action.title,
                    start=action.start_iso,
                    end=action.end_iso,
                    duration_minutes=action.duration_minutes,
                    calendar_category=action.calendar_category,
                    location_query=action.location_query,
                    location=action.location,
                    description=action.description,
                )
                actions_taken.append(self._calendar_event_audit(fixed_event))
                continue
            if action.action is ActionType.CREATE_TASK:
                current_task, audit = self._create_or_resolve(action, timezone)
                if audit is not None:
                    actions_taken.append(audit)
                continue
            if current_task is None:
                if action.task_id is None:
                    raise InteractionError("Schedule action has no task")
                current_task = self.vikunja.get_task(action.task_id)
            scheduled = self._schedule(current_task, action, now, timezone)
            actions_taken.append(self._schedule_audit(current_task, scheduled))

        result = self._result(
            brief,
            scheduled,
            current_task,
            fixed_event,
            timezone,
        )
        return InteractResponse(
            result=result,
            intent=plan.intent,
            plan=plan,
            actions_taken=actions_taken,
            brief=brief,
            schedule=scheduled,
            task=current_task,
            calendar_event=fixed_event,
        )

    def _create_or_resolve(
        self, action: PlannedAction, timezone: ZoneInfo
    ) -> tuple[VikunjaTask, InteractionAction | None]:
        task = self._find_existing(action.title or "") if action.reuse_existing else None
        if task is None:
            if action.reuse_existing and action.deadline is None:
                raise InteractionTaskNotFound(
                    f'No incomplete Vikunja task matched "{action.title}"; '
                    "provide a date before Beacon creates and schedules it."
                )
            task = self.vikunja.create_task(
                action.title or "", self._due_datetime(action.deadline, timezone)
            )
            audit = InteractionAction(
                action="task_created",
                status="CREATED",
                target=f"vikunja-task:{task.id}",
            )
        else:
            audit = None
        return task, audit

    def _schedule(
        self,
        task: VikunjaTask,
        action: PlannedAction,
        now: datetime,
        timezone: ZoneInfo,
    ) -> ScheduleTaskResponse:
        earliest, deadline = self._schedule_bounds(
            action.deadline,
            now,
            timezone,
            action.window_start,
            action.window_end,
        )
        if action.duration_minutes is None:
            raise InteractionError("Schedule action has no duration")
        return self.scheduler.schedule_task(
            task,
            ScheduleTaskRequest(
                duration_minutes=action.duration_minutes,
                earliest_iso=earliest,
                deadline_iso=deadline,
                create_event=action.create_event,
            ),
        )

    def _find_existing(self, title: str) -> VikunjaTask | None:
        query = self._normalize_title(title)
        candidates = [task for task in self.vikunja.list_tasks() if not task.done]
        exact = [task for task in candidates if self._normalize_title(task.title) == query]
        matches = exact or [
            task for task in candidates if query in self._normalize_title(task.title)
        ]
        if len(matches) > 1:
            choices = ", ".join(f"{task.id}: {task.title}" for task in matches[:5])
            raise AmbiguousTaskError(
                f'Multiple Vikunja tasks matched "{title}": {choices}'
            )
        return matches[0] if matches else None

    @staticmethod
    def _brief_audit(brief: DailyBriefResponse) -> InteractionAction:
        return InteractionAction(
            action="brief_generated",
            status="READ_ONLY",
            target=str(brief.date),
            details={
                "events": brief.summary.event_count,
                "work_blocks": brief.summary.work_block_count,
                "overdue_tasks": brief.summary.overdue_task_count,
                "conflicts": brief.summary.conflict_count,
            },
        )

    @staticmethod
    def _schedule_audit(
        task: VikunjaTask, scheduled: ScheduleTaskResponse
    ) -> InteractionAction:
        option = scheduled.selected_option
        return InteractionAction(
            action="task_scheduled",
            status=scheduled.status.value,
            target=f"vikunja-task:{task.id}",
            details={
                "start_iso": option.start_iso.isoformat(),
                "end_iso": option.end_iso.isoformat(),
            },
        )

    @staticmethod
    def _calendar_event_audit(
        response: CreateCalendarEventResponse,
    ) -> InteractionAction:
        event = response.event
        if response.status is CalendarEventCreateStatus.CLARIFICATION:
            resolution = response.location_resolution
            return InteractionAction(
                action="calendar_event_clarification",
                status="PENDING",
                target=(resolution.query if resolution else None),
                details={
                    "candidates": (
                        len(resolution.alternatives) if resolution else 0
                    )
                },
            )
        if event is None:
            raise InteractionError("Calendar event result is missing its event")
        return InteractionAction(
            action="calendar_event_created",
            status=response.status.value,
            target=(f"calendar-event:{event.uid}" if event.uid else event.title),
            details={
                "calendar": event.calendar,
                "start_iso": event.start_iso.isoformat(),
                "end_iso": event.end_iso.isoformat(),
                "conflicts": len(response.conflicts),
            },
        )

    @staticmethod
    def _result(
        brief,
        scheduled,
        task,
        fixed_event: CreateCalendarEventResponse | None,
        timezone: ZoneInfo,
    ) -> str:
        if fixed_event is not None:
            return ActionExecutor._calendar_event_result(fixed_event, timezone)
        if brief is not None:
            return brief.spoken_summary
        if scheduled is not None and task is not None:
            option = scheduled.selected_option
            verb = {
                "NEW": "Scheduled",
                "UPDATED": "Rescheduled",
                "UNCHANGED": "Already scheduled",
                "RECOMMENDATION_ONLY": "Recommended",
            }[scheduled.status.value]
            return (
                f'{verb} "{task.title}" from '
                f"{option.start_iso.astimezone(timezone).isoformat()} to "
                f"{option.end_iso.astimezone(timezone).isoformat()}."
            )
        if task is not None:
            return f'Created task "{task.title}".'
        raise InteractionError("Action plan produced no result")

    @staticmethod
    def _calendar_event_result(
        response: CreateCalendarEventResponse,
        timezone: ZoneInfo,
    ) -> str:
        if response.status is CalendarEventCreateStatus.CLARIFICATION:
            return response.clarification_question or (
                "Please provide a more specific venue."
            )
        event = response.event
        if event is None:
            raise InteractionError("Calendar event result is missing its event")
        start = event.start_iso.astimezone(timezone)
        end = event.end_iso.astimezone(timezone)
        date_text = f"{start:%A, %B} {start.day}, {start.year}"
        start_text = start.strftime("%I:%M %p").lstrip("0")
        end_text = end.strftime("%I:%M %p").lstrip("0")
        prefix = (
            "Created calendar event"
            if response.status is CalendarEventCreateStatus.CREATED
            else "Calendar event already exists:"
        )
        result = (
            f'{prefix} "{event.title}" on {date_text} from '
            f"{start_text} to {end_text} in {event.calendar.title()}"
        )
        if event.location:
            result += f" at {event.location}"
        result += "."
        warnings = list(response.notices)
        warnings.extend(f"Warning: {warning}" for warning in response.warnings)
        for conflict in response.conflicts:
            conflict_start = conflict.start_iso.astimezone(timezone).strftime(
                "%I:%M %p"
            ).lstrip("0")
            conflict_end = conflict.end_iso.astimezone(timezone).strftime(
                "%I:%M %p"
            ).lstrip("0")
            warnings.append(
                f'Warning: This overlaps with "{conflict.title}" from '
                f"{conflict_start} to {conflict_end} in {conflict.calendar}."
            )
        return "\n".join([result, *warnings])

    @staticmethod
    def _schedule_bounds(
        target_date: date | None,
        now: datetime,
        timezone: ZoneInfo,
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> tuple[datetime, datetime | None]:
        if target_date is None:
            return now, None
        start = time.fromisoformat(window_start or "09:00")
        end = time.fromisoformat(window_end or "22:00")
        earliest = datetime.combine(target_date, start, tzinfo=timezone)
        deadline = datetime.combine(target_date, end, tzinfo=timezone)
        if target_date == now.date():
            earliest = max(earliest, now)
        return earliest, deadline

    @staticmethod
    def _due_datetime(deadline: date | None, timezone: ZoneInfo) -> datetime | None:
        if deadline is None:
            return None
        return datetime.combine(deadline, time(22, 0), tzinfo=timezone)

    @staticmethod
    def _normalize_title(value: str) -> str:
        return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())
