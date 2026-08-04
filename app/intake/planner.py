from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.models import (
    ActionPlan,
    ActionType,
    IntentType,
    PlannedAction,
    StructuredIntent,
)


class ActionPlanner:
    """Pure Beacon policy: convert intent into deterministic service operations."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def plan(self, intent: StructuredIntent, today: date) -> ActionPlan:
        if intent.intent is IntentType.CREATE_CALENDAR_EVENTS:
            return self._plan_daily_calendar_events(intent)

        relative_date, window_start, window_end, supported = self._time_constraint(
            intent.time_constraint, today
        )
        deadline = intent.deadline or relative_date

        if not supported:
            return ActionPlan(
                intent=intent,
                actions=[
                    PlannedAction(
                        action=ActionType.REQUEST_CLARIFICATION,
                        question=(
                            f'I cannot safely apply the time constraint '
                            f'"{intent.time_constraint}" yet. What date or part of day '
                            "should Beacon use?"
                        ),
                    )
                ],
            )

        if intent.intent is IntentType.UNKNOWN:
            return ActionPlan(
                intent=intent,
                actions=[
                    PlannedAction(
                        action=ActionType.REQUEST_CLARIFICATION,
                        question=intent.clarification_question,
                    )
                ],
            )
        if intent.intent is IntentType.QUERY_CONTEXT:
            return ActionPlan(
                intent=intent,
                actions=[
                    PlannedAction(
                        action=ActionType.QUERY_CONTEXT,
                        context_operation=intent.operation,
                        entity_reference=intent.entity_reference,
                    )
                ],
            )
        if intent.intent in (IntentType.STORE_CONTEXT, IntentType.FORGET_CONTEXT):
            return ActionPlan(
                intent=intent,
                actions=[
                    PlannedAction(
                        action=ActionType.MUTATE_CONTEXT,
                        context_operation=intent.operation,
                        entity=intent.entity,
                        source_entity=intent.source_entity,
                        target_entity=intent.target_entity,
                        entity_reference=intent.entity_reference,
                        target_reference=intent.target_reference,
                        alias=intent.alias,
                        predicate=intent.predicate,
                        value=intent.value,
                        value_reference=intent.value_reference,
                        relationship=intent.relationship,
                        provenance=intent.provenance,
                        source_reference=intent.source_reference,
                        replace_existing=intent.replace_existing,
                    )
                ],
            )
        if intent.intent is IntentType.BRIEF:
            return ActionPlan(
                intent=intent,
                actions=[
                    PlannedAction(
                        action=ActionType.GENERATE_BRIEF,
                        deadline=deadline,
                    )
                ],
            )
        if intent.intent is IntentType.CREATE_TASK:
            return ActionPlan(
                intent=intent,
                actions=[
                    PlannedAction(
                        action=ActionType.CREATE_TASK,
                        title=intent.title,
                        deadline=deadline,
                    )
                ],
            )

        actions: list[PlannedAction] = []
        if intent.task_id is None:
            actions.append(
                PlannedAction(
                    action=ActionType.CREATE_TASK,
                    title=intent.title,
                    deadline=deadline,
                    reuse_existing=True,
                )
            )
        actions.append(
            PlannedAction(
                action=ActionType.SCHEDULE_WORK_BLOCK,
                task_id=intent.task_id,
                deadline=deadline,
                window_start=window_start,
                window_end=window_end,
                duration_minutes=(
                    intent.duration_minutes
                    or self.settings.beacon_interaction_default_duration_minutes
                ),
                create_event=intent.create_event,
            )
        )
        return ActionPlan(intent=intent, actions=actions)

    def _plan_daily_calendar_events(
        self, intent: StructuredIntent
    ) -> ActionPlan:
        event_range = intent.daily_event_range
        if event_range is None or not intent.title:
            raise ValueError("daily_range_missing_required_fields")
        if event_range.end_date < event_range.start_date:
            raise ValueError("daily_range_end_before_start")
        if event_range.daily_end_time <= event_range.daily_start_time:
            raise ValueError("daily_range_end_time_not_after_start_time")
        occurrence_count = (event_range.end_date - event_range.start_date).days + 1
        if occurrence_count > self.settings.beacon_max_daily_range_occurrences:
            raise ValueError(
                "daily_range_occurrence_limit_exceeded: "
                f"{occurrence_count} > "
                f"{self.settings.beacon_max_daily_range_occurrences}"
            )

        timezone = ZoneInfo(self.settings.beacon_timezone)
        actions = []
        for offset in range(occurrence_count):
            occurrence_date = event_range.start_date + timedelta(days=offset)
            actions.append(
                PlannedAction(
                    action=ActionType.CREATE_CALENDAR_EVENT,
                    title=intent.title,
                    description=intent.description,
                    calendar_name=(
                        intent.calendar_name
                        or self.settings.beacon_schedule_calendar
                    ),
                    start_iso=datetime.combine(
                        occurrence_date,
                        event_range.daily_start_time,
                        tzinfo=timezone,
                    ),
                    end_iso=datetime.combine(
                        occurrence_date,
                        event_range.daily_end_time,
                        tzinfo=timezone,
                    ),
                    source_reference=intent.source_reference,
                )
            )
        return ActionPlan(intent=intent, actions=actions)

    @staticmethod
    def _time_constraint(
        value: str | None, today: date
    ) -> tuple[date | None, str | None, str | None, bool]:
        if value is None:
            return None, None, None, True
        normalized = " ".join(value.casefold().split())
        relative_date = None
        if "tomorrow" in normalized:
            relative_date = today + timedelta(days=1)
            normalized = normalized.replace("tomorrow", "").strip()
        elif "today" in normalized:
            relative_date = today
            normalized = normalized.replace("today", "").strip()

        windows = {
            "": (None, None),
            "morning": ("09:00", "12:00"),
            "afternoon": ("12:00", "17:00"),
            "evening": ("17:00", "22:00"),
        }
        if normalized not in windows:
            return relative_date, None, None, False
        start, end = windows[normalized]
        return relative_date, start, end, relative_date is not None or bool(normalized)
