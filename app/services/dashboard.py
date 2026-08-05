from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.models import (
    BriefCalendarEvent,
    DashboardAttentionItem,
    DashboardAttentionSeverity,
    DashboardAttentionSource,
    DashboardEventSummary,
    DashboardTaskPriority,
    DashboardTaskSummary,
    DailyBriefResponse,
    TodayDashboardResponse,
    TravelEstimate,
    VikunjaTask,
)
from app.services.daily_brief import DailyBriefService


class TodayDashboardService:
    """Adapt Beacon's deterministic Daily Brief into the native read model."""

    PRIORITY_TASK_LIMIT = 5

    def __init__(
        self,
        daily_brief: DailyBriefService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.daily_brief = daily_brief or DailyBriefService(settings=self.settings)

    def build(self) -> TodayDashboardResponse:
        brief = self.daily_brief.build()
        timezone = ZoneInfo(brief.timezone)
        priority_tasks = self._priority_tasks(brief, timezone)
        return TodayDashboardResponse(
            generated_at=self._local_datetime(brief.generated_at, timezone),
            timezone=brief.timezone,
            local_date=brief.date,
            display_name=None,
            next_event=self._event_summary(
                brief.summary.next_event,
                brief.travel,
                timezone,
            ),
            focus=None,
            attention_items=self._attention_items(brief, timezone),
            priority_tasks=priority_tasks,
            recommended_action=None,
        )

    def _priority_tasks(
        self,
        brief: DailyBriefResponse,
        timezone: ZoneInfo,
    ) -> list[DashboardTaskSummary]:
        candidates = [
            brief.tasks.highest_priority,
            *brief.tasks.overdue,
            *brief.tasks.due_today,
        ]
        summaries: list[DashboardTaskSummary] = []
        seen: set[int] = set()
        for task in candidates:
            if task is None or task.done or task.id in seen:
                continue
            summaries.append(self._task_summary(task, timezone))
            seen.add(task.id)
            if len(summaries) == self.PRIORITY_TASK_LIMIT:
                break
        return summaries

    def _attention_items(
        self,
        brief: DailyBriefResponse,
        timezone: ZoneInfo,
    ) -> list[DashboardAttentionItem]:
        return [
            DashboardAttentionItem(
                id=f"overdue_task:{task.id}",
                title=task.title,
                detail=(
                    f"Due {self._local_datetime(task.due_date, timezone).isoformat()}"
                    if task.due_date
                    else None
                ),
                severity=DashboardAttentionSeverity.WARNING,
                source=DashboardAttentionSource.TASK,
            )
            for task in brief.tasks.overdue
            if not task.done
        ]

    def _event_summary(
        self,
        event: BriefCalendarEvent | None,
        travel: list[TravelEstimate],
        timezone: ZoneInfo,
    ) -> DashboardEventSummary | None:
        if event is None:
            return None
        start_at = self._local_datetime(event.start_iso, timezone)
        end_at = self._local_datetime(event.end_iso, timezone)
        leave_by = self._leave_by(event, travel, timezone)
        return DashboardEventSummary(
            id=event.uid or self._fallback_event_id(event, start_at, end_at),
            title=event.title,
            start_at=start_at,
            end_at=end_at,
            location=event.location,
            leave_by_at=leave_by,
            calendar_name=event.calendar,
        )

    def _task_summary(
        self,
        task: VikunjaTask,
        timezone: ZoneInfo,
    ) -> DashboardTaskSummary:
        return DashboardTaskSummary(
            id=str(task.id),
            title=task.title,
            project_name=None,
            priority=self._priority(task.priority),
            due_at=(
                self._local_datetime(task.due_date, timezone)
                if task.due_date
                else None
            ),
            completed=task.done,
        )

    @staticmethod
    def _priority(value: int) -> DashboardTaskPriority:
        mapping = {
            1: DashboardTaskPriority.LOW,
            2: DashboardTaskPriority.MEDIUM,
            3: DashboardTaskPriority.HIGH,
            4: DashboardTaskPriority.URGENT,
        }
        if value <= 0:
            return DashboardTaskPriority.NONE
        return mapping.get(value, DashboardTaskPriority.DO_NOW)

    @staticmethod
    def _leave_by(
        event: BriefCalendarEvent,
        travel: list[TravelEstimate],
        timezone: ZoneInfo,
    ) -> datetime | None:
        for estimate in travel:
            same_event = (
                estimate.event_uid == event.uid
                if event.uid is not None
                else estimate.event_uid is None
                and estimate.event_title == event.title
            )
            if same_event:
                return TodayDashboardService._local_datetime(
                    estimate.leave_by, timezone
                )
        return None

    @staticmethod
    def _fallback_event_id(
        event: BriefCalendarEvent,
        start_at: datetime,
        end_at: datetime,
    ) -> str:
        identity = "\x1f".join(
            (
                event.calendar,
                event.title,
                start_at.isoformat(),
                end_at.isoformat(),
            )
        )
        return f"event:{sha256(identity.encode()).hexdigest()}"

    @staticmethod
    def _local_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone)
        return value.astimezone(timezone)
