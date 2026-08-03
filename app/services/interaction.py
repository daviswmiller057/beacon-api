import re
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.models import (
    DailyBriefResponse,
    InteractRequest,
    InteractResponse,
    InteractionAction,
    IntentType,
    ScheduleTaskRequest,
    ScheduleTaskResponse,
    StructuredIntent,
    VikunjaTask,
)
from app.services.daily_brief import DailyBriefService
from app.services.scheduler import SchedulerService
from app.services.vikunja_client import VikunjaClient


class InteractionError(RuntimeError):
    pass


class UnsupportedIntentError(InteractionError):
    pass


class InteractionTaskNotFound(InteractionError):
    pass


class AmbiguousTaskError(InteractionError):
    pass


class RuleBasedIntentInterpreter:
    """Small, deterministic fallback for Beacon's minimum useful commands."""

    _duration = re.compile(
        r"\bfor\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\b",
        re.IGNORECASE,
    )
    _task_id = re.compile(r"(?:\btask\s+|#)(\d+)\b", re.IGNORECASE)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def interpret(self, message: str, today: date) -> StructuredIntent:
        compact = " ".join(message.strip().split())
        lowered = compact.casefold()
        target_date = self._target_date(lowered, today)

        is_schedule = bool(re.match(r"^(please\s+)?schedule\b", lowered))
        if not is_schedule and self._is_brief_request(lowered):
            return StructuredIntent(
                action=IntentType.BRIEF,
                target_date=target_date,
            )
        if not is_schedule:
            raise UnsupportedIntentError(
                "I can currently produce a brief or schedule a Vikunja task."
            )

        duration, compact = self._extract_duration(compact)
        id_match = self._task_id.search(compact)
        if id_match:
            return StructuredIntent(
                action=IntentType.SCHEDULE_TASK,
                task_id=int(id_match.group(1)),
                target_date=target_date,
                duration_minutes=duration,
            )

        title = re.sub(
            r"^(please\s+)?schedule\s+", "", compact, flags=re.IGNORECASE
        )
        title = re.sub(
            r"\b(?:(?:for|on)\s+)?(?:today|tomorrow)\b",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(
            r"\s+(?:on|in)\s+(?:my\s+)?calendar\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = " ".join(title.strip(" .,!?").split())
        if not title:
            raise UnsupportedIntentError(
                "Name the Vikunja task to schedule, or use its task ID."
            )
        return StructuredIntent(
            action=IntentType.SCHEDULE_TASK,
            task_title=title,
            target_date=target_date,
            duration_minutes=duration,
        )

    def _extract_duration(self, message: str) -> tuple[int, str]:
        match = self._duration.search(message)
        if not match:
            return self.settings.beacon_interaction_default_duration_minutes, message
        amount = float(match.group(1))
        unit = match.group(2).casefold()
        minutes = round(amount * 60) if unit.startswith("h") else round(amount)
        if not 1 <= minutes <= 1440:
            raise UnsupportedIntentError(
                "Work block duration must be between 1 minute and 24 hours."
            )
        return minutes, (message[: match.start()] + message[match.end() :])

    @staticmethod
    def _target_date(message: str, today: date) -> date | None:
        if re.search(r"\btomorrow\b", message):
            return today + timedelta(days=1)
        if re.search(r"\btoday\b", message):
            return today
        return None

    @staticmethod
    def _is_brief_request(message: str) -> bool:
        return any(
            phrase in message
            for phrase in (
                "brief",
                "what's on",
                "what is on",
                "what do i have",
                "my day",
                "status",
            )
        )


class InteractionService:
    def __init__(
        self,
        *,
        vikunja: VikunjaClient | None = None,
        scheduler: SchedulerService | None = None,
        daily_brief: DailyBriefService | None = None,
        settings: Settings | None = None,
        clock: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vikunja = vikunja or VikunjaClient()
        self.scheduler = scheduler or SchedulerService()
        self.daily_brief = daily_brief or DailyBriefService()
        self.clock = clock or (lambda timezone: datetime.now(timezone))
        self.interpreter = RuleBasedIntentInterpreter(self.settings)

    def interact(self, request: InteractRequest) -> InteractResponse:
        timezone = ZoneInfo(self.settings.beacon_timezone)
        now = self.clock(timezone).astimezone(timezone)
        intent = request.intent or self.interpreter.interpret(
            request.message or "", now.date()
        )
        if intent.action is IntentType.BRIEF:
            return self._brief(intent)
        return self._schedule(intent, now, timezone)

    def _brief(self, intent: StructuredIntent) -> InteractResponse:
        brief = self.daily_brief.build(intent.target_date)
        return InteractResponse(
            result=brief.spoken_summary,
            intent=intent,
            actions_taken=[
                InteractionAction(
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
            ],
            brief=brief,
        )

    def _schedule(
        self,
        intent: StructuredIntent,
        now: datetime,
        timezone: ZoneInfo,
    ) -> InteractResponse:
        task = self._resolve_task(intent)
        earliest, deadline = self._schedule_bounds(intent.target_date, now, timezone)
        schedule_request = ScheduleTaskRequest(
            duration_minutes=(
                intent.duration_minutes
                or self.settings.beacon_interaction_default_duration_minutes
            ),
            earliest_iso=earliest,
            deadline_iso=deadline,
            create_event=intent.create_event,
        )
        scheduled = self.scheduler.schedule_task(task, schedule_request)
        option = scheduled.selected_option
        verb = {
            "NEW": "Scheduled",
            "UPDATED": "Rescheduled",
            "UNCHANGED": "Already scheduled",
            "RECOMMENDATION_ONLY": "Recommended",
        }[scheduled.status.value]
        result = (
            f'{verb} "{task.title}" from '
            f"{option.start_iso.astimezone(timezone).isoformat()} to "
            f"{option.end_iso.astimezone(timezone).isoformat()}."
        )
        return InteractResponse(
            result=result,
            intent=intent,
            actions_taken=[
                InteractionAction(
                    action="task_scheduled",
                    status=scheduled.status.value,
                    target=f"vikunja-task:{task.id}",
                    details={
                        "calendar": (
                            scheduled.calendar_event.calendar
                            if scheduled.calendar_event
                            else self.settings.beacon_schedule_calendar
                        ),
                        "start_iso": option.start_iso.isoformat(),
                        "end_iso": option.end_iso.isoformat(),
                    },
                )
            ],
            schedule=scheduled,
        )

    def _resolve_task(self, intent: StructuredIntent) -> VikunjaTask:
        if intent.task_id is not None:
            return self.vikunja.get_task(intent.task_id)
        query = self._normalize_title(intent.task_title or "")
        candidates = [task for task in self.vikunja.list_tasks() if not task.done]
        exact = [
            task for task in candidates if self._normalize_title(task.title) == query
        ]
        matches = exact or [
            task for task in candidates if query in self._normalize_title(task.title)
        ]
        if not matches:
            raise InteractionTaskNotFound(
                f'No incomplete Vikunja task matched "{intent.task_title}".'
            )
        if len(matches) > 1:
            choices = ", ".join(f"{task.id}: {task.title}" for task in matches[:5])
            raise AmbiguousTaskError(
                f'Multiple Vikunja tasks matched "{intent.task_title}": {choices}'
            )
        return matches[0]

    def _schedule_bounds(
        self,
        target_date: date | None,
        now: datetime,
        timezone: ZoneInfo,
    ) -> tuple[datetime, datetime | None]:
        if target_date is None:
            return now, None
        day_start = self._parse_time("09:00")
        day_end = self._parse_time("22:00")
        earliest = datetime.combine(target_date, day_start, tzinfo=timezone)
        deadline = datetime.combine(target_date, day_end, tzinfo=timezone)
        if target_date == now.date():
            earliest = max(earliest, now)
        return earliest, deadline

    @staticmethod
    def _parse_time(value: str) -> time:
        return time.fromisoformat(value)

    @staticmethod
    def _normalize_title(value: str) -> str:
        return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())
