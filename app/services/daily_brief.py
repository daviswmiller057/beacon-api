from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.models import (
    BriefCalendarEvent,
    BriefConflict,
    BriefConflictType,
    BriefWarning,
    BriefWarningSource,
    DailyBriefCalendar,
    DailyBriefResponse,
    DailyBriefSummary,
    DailyBriefTasks,
    TravelEstimate,
    VikunjaTask,
    WeatherConditions,
)
from app.services.caldav_client import CalDAVService
from app.services.home_assistant_client import (
    HomeAssistantClient,
    HomeAssistantError,
)
from app.services.vikunja_client import VikunjaClient
from app.services.waze_client import WazeClient, WazeError


class DailyBriefService:
    def __init__(
        self,
        caldav: CalDAVService | None = None,
        vikunja: VikunjaClient | None = None,
        waze: WazeClient | None = None,
        home_assistant: HomeAssistantClient | None = None,
        settings: Settings | None = None,
        clock: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.caldav = caldav or CalDAVService()
        self.vikunja = vikunja or VikunjaClient()
        self.waze = waze
        self.home_assistant = home_assistant
        self.clock = clock or (lambda timezone: datetime.now(timezone))

    def build(self, requested_date: date | None = None) -> DailyBriefResponse:
        timezone = ZoneInfo(self.settings.beacon_timezone)
        generated_at = self.clock(timezone).astimezone(timezone)
        target_date = requested_date or generated_at.date()
        day_start = datetime.combine(target_date, time.min, tzinfo=timezone)
        day_end = day_start + timedelta(days=1)
        warnings: list[BriefWarning] = []

        try:
            all_events = self.caldav.fetch_calendar_events(
                day_start,
                day_end,
                calendar_names=self.settings.calendar_names,
            )
        except Exception as exc:
            all_events = []
            warnings.append(
                self._warning(
                    BriefWarningSource.CALENDAR,
                    "CALENDAR_UNAVAILABLE",
                    f"Calendar data is unavailable: {exc}",
                )
            )

        all_events = sorted(
            all_events,
            key=lambda event: (
                event.start_iso,
                event.end_iso,
                event.calendar.casefold(),
                event.title.casefold(),
            ),
        )

        try:
            all_tasks = self.vikunja.list_tasks()
        except Exception as exc:
            all_tasks = []
            warnings.append(
                self._warning(
                    BriefWarningSource.VIKUNJA,
                    "VIKUNJA_UNAVAILABLE",
                    f"Task data is unavailable: {exc}",
                )
            )

        events = [item for item in all_events if not item.is_beacon_work_block]
        work_blocks = [item for item in all_events if item.is_beacon_work_block]
        task_groups = self._group_tasks(all_tasks, target_date, timezone)
        travel, travel_conflicts, travel_warnings = self._build_travel(
            all_events, generated_at
        )
        warnings.extend(travel_warnings)
        weather, weather_warnings = self._build_weather()
        warnings.extend(weather_warnings)
        conflicts = self._overlap_conflicts(all_events) + travel_conflicts
        next_event = self._next_event(
            events, target_date, generated_at, timezone
        )
        summary = DailyBriefSummary(
            event_count=len(events),
            work_block_count=len(work_blocks),
            overdue_task_count=len(task_groups.overdue),
            due_today_task_count=len(task_groups.due_today),
            conflict_count=len(conflicts),
            next_event=next_event,
            highest_priority_task=task_groups.highest_priority,
        )
        spoken = self._spoken_summary(
            summary,
            work_blocks,
            travel,
            weather,
            timezone,
        )
        return DailyBriefResponse(
            date=target_date,
            timezone=self.settings.beacon_timezone,
            generated_at=generated_at,
            calendar=DailyBriefCalendar(
                events=events,
                work_blocks=work_blocks,
            ),
            tasks=task_groups,
            travel=travel,
            weather=weather,
            warnings=warnings,
            conflicts=conflicts,
            summary=summary,
            spoken_summary=spoken,
        )

    def _group_tasks(
        self,
        tasks: list[VikunjaTask],
        target_date: date,
        timezone: ZoneInfo,
    ) -> DailyBriefTasks:
        incomplete = [task for task in tasks if not task.done]
        overdue = sorted(
            [
                task
                for task in incomplete
                if task.due_date
                and self._local_datetime(task.due_date, timezone).date()
                < target_date
            ],
            key=lambda task: self._task_order(task, timezone),
        )
        due_today = sorted(
            [
                task
                for task in incomplete
                if task.due_date
                and self._local_datetime(task.due_date, timezone).date()
                == target_date
            ],
            key=lambda task: self._task_order(task, timezone),
        )
        highest = (
            min(incomplete, key=lambda task: self._task_order(task, timezone))
            if incomplete
            else None
        )
        return DailyBriefTasks(
            overdue=overdue,
            due_today=due_today,
            highest_priority=highest,
        )

    @staticmethod
    def _task_order(task: VikunjaTask, timezone: ZoneInfo) -> tuple:
        due_timestamp = (
            DailyBriefService._local_datetime(task.due_date, timezone).timestamp()
            if task.due_date
            else float("inf")
        )
        return (-task.priority, due_timestamp, task.id)

    @staticmethod
    def _local_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone)
        return value.astimezone(timezone)

    def _build_travel(
        self,
        events: list[BriefCalendarEvent],
        generated_at: datetime,
    ) -> tuple[list[TravelEstimate], list[BriefConflict], list[BriefWarning]]:
        if not self.settings.daily_brief_travel_enabled:
            return [], [], []
        if not self.settings.beacon_home_location:
            return [], [], [
                self._warning(
                    BriefWarningSource.WAZE,
                    "TRAVEL_NOT_CONFIGURED",
                    "Travel is enabled but Beacon home location is not configured.",
                )
            ]
        client = self.waze or WazeClient()
        buffer_minutes = self.settings.daily_brief_travel_buffer_minutes
        estimates: list[TravelEstimate] = []
        conflicts: list[BriefConflict] = []
        warnings: list[BriefWarning] = []
        located = [event for event in events if event.location]

        for event in located:
            try:
                estimate = client.estimate(
                    self.settings.beacon_home_location,
                    event.location,
                    event,
                    buffer_minutes,
                )
                estimates.append(estimate)
                if estimate.leave_by < generated_at:
                    conflicts.append(
                        BriefConflict(
                            type=BriefConflictType.LEAVE_BY_PASSED,
                            message=f"Leave-by time for {event.title} has passed.",
                            event_uids=self._uids(event),
                        )
                    )
            except WazeError as exc:
                warnings.append(
                    self._warning(
                        BriefWarningSource.WAZE,
                        "TRAVEL_ESTIMATE_FAILED",
                        str(exc),
                    )
                )

        for previous, current in zip(events, events[1:]):
            if not previous.location or not current.location:
                continue
            try:
                travel_minutes = client.travel_minutes(
                    previous.location, current.location
                )
                if previous.end_iso + timedelta(minutes=travel_minutes) > current.start_iso:
                    conflicts.append(
                        BriefConflict(
                            type=BriefConflictType.INSUFFICIENT_TRAVEL_TIME,
                            message=(
                                f"There is insufficient travel time between "
                                f"{previous.title} and {current.title}."
                            ),
                            event_uids=self._uids(previous, current),
                        )
                    )
            except WazeError as exc:
                warnings.append(
                    self._warning(
                        BriefWarningSource.WAZE,
                        "SEQUENTIAL_TRAVEL_FAILED",
                        str(exc),
                    )
                )
        return estimates, conflicts, warnings

    def _build_weather(
        self,
    ) -> tuple[WeatherConditions | None, list[BriefWarning]]:
        if not self.settings.daily_brief_weather_enabled:
            return None, []
        try:
            client = self.home_assistant or HomeAssistantClient()
            return client.get_weather(), []
        except HomeAssistantError as exc:
            return None, [
                self._warning(
                    BriefWarningSource.HOME_ASSISTANT,
                    "WEATHER_UNAVAILABLE",
                    str(exc),
                )
            ]

    def _overlap_conflicts(
        self,
        events: list[BriefCalendarEvent],
    ) -> list[BriefConflict]:
        conflicts: list[BriefConflict] = []
        for index, first in enumerate(events):
            for second in events[index + 1 :]:
                if second.start_iso >= first.end_iso:
                    break
                if first.start_iso >= second.end_iso:
                    continue
                work_overlap = (
                    first.is_beacon_work_block != second.is_beacon_work_block
                )
                conflict_type = (
                    BriefConflictType.WORK_BLOCK_OVERLAP
                    if work_overlap
                    else BriefConflictType.OVERLAPPING_EVENTS
                )
                conflicts.append(
                    BriefConflict(
                        type=conflict_type,
                        message=f"{first.title} overlaps {second.title}.",
                        event_uids=self._uids(first, second),
                    )
                )
        return conflicts

    @staticmethod
    def _next_event(
        events: list[BriefCalendarEvent],
        target_date: date,
        generated_at: datetime,
        timezone: ZoneInfo,
    ) -> BriefCalendarEvent | None:
        if not events:
            return None
        if target_date == generated_at.astimezone(timezone).date():
            return next(
                (event for event in events if event.end_iso > generated_at),
                None,
            )
        return events[0]

    def _spoken_summary(
        self,
        summary: DailyBriefSummary,
        work_blocks: list[BriefCalendarEvent],
        travel: list[TravelEstimate],
        weather: WeatherConditions | None,
        timezone: ZoneInfo,
    ) -> str:
        sentences = ["Good morning."]
        if summary.next_event:
            sentences.append(
                f"You have {summary.next_event.title} at "
                f"{self._format_time(summary.next_event.start_iso, timezone)}."
            )
            estimate = next(
                (
                    item
                    for item in travel
                    if item.event_uid == summary.next_event.uid
                    and item.event_title == summary.next_event.title
                ),
                None,
            )
            if estimate:
                sentences.append(
                    f"Leave by {self._format_time(estimate.leave_by, timezone)} due to traffic."
                )
        if summary.highest_priority_task:
            task = summary.highest_priority_task
            sentences.append(f"Your highest priority is {task.title}.")
        if summary.overdue_task_count:
            noun = "task" if summary.overdue_task_count == 1 else "tasks"
            sentences.append(
                f"You have {summary.overdue_task_count} overdue {noun}."
            )
        if work_blocks:
            block = work_blocks[0]
            sentences.append(
                "You have a Beacon work block from "
                f"{self._format_time(block.start_iso, timezone)} to "
                f"{self._format_time(block.end_iso, timezone)}."
            )
        if weather:
            weather_text = f"Current conditions are {weather.condition}"
            if weather.temperature is not None:
                weather_text += f" at {weather.temperature:g} degrees"
            sentences.append(weather_text + ".")
        if summary.conflict_count:
            noun = "conflict" if summary.conflict_count == 1 else "conflicts"
            sentences.append(
                f"There are {summary.conflict_count} schedule {noun}."
            )
        else:
            sentences.append("No schedule conflicts were found.")
        return " ".join(sentences)

    @staticmethod
    def _format_time(value: datetime, timezone: ZoneInfo) -> str:
        local = value.astimezone(timezone)
        if local.minute:
            return local.strftime("%I:%M %p").lstrip("0")
        return local.strftime("%I %p").lstrip("0")

    @staticmethod
    def _warning(
        source: BriefWarningSource,
        code: str,
        message: str,
    ) -> BriefWarning:
        return BriefWarning(source=source, code=code, message=message)

    @staticmethod
    def _uids(*events: BriefCalendarEvent) -> list[str]:
        return [event.uid for event in events if event.uid]
