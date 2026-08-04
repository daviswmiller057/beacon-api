import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.intake.executor import ActionExecutor
from app.intake.gemini import GeminiInterpreter
from app.intake.planner import ActionPlanner
from app.intake.rules import RuleBasedIntentInterpreter
from app.models import (
    ActionType,
    CalendarBatchStatus,
    CalendarEventCreateRequest,
    CalendarEventResult,
    DailyEventRange,
    InteractRequest,
    IntentType,
    StructuredIntent,
)
from app.services.scheduler import CalendarEventCreationError, SchedulerService
from app.services.interaction import InteractionService


ZONE = ZoneInfo("America/Chicago")
NOW = datetime(2026, 8, 1, 8, 0, tzinfo=ZONE)
EXAMPLE = (
    "Houston Ballet maintenance calls August 17 through August 21, 2026, "
    "from 9:00 AM to 5:00 PM each day"
)


def settings(**changes) -> Settings:
    values = {
        "beacon_api_key": "test",
        "nextcloud_caldav_url": "https://example.invalid/caldav",
        "nextcloud_username": "test",
        "nextcloud_app_password": "test",
        "vikunja_api_url": "https://example.invalid/api/v1",
        "vikunja_api_token": "test",
        "beacon_calendars": "personal",
        "beacon_schedule_calendar": "personal",
        "beacon_timezone": "America/Chicago",
        "beacon_interpreter": "rules",
    }
    values.update(changes)
    return Settings(**values)


def range_intent(
    start_date=date(2026, 8, 17),
    end_date=date(2026, 8, 21),
    start_time=time(9),
    end_time=time(17),
) -> StructuredIntent:
    return StructuredIntent(
        intent=IntentType.CREATE_CALENDAR_EVENTS,
        title="Houston Ballet maintenance calls",
        description="Synthetic planning test",
        calendar_name="personal",
        source_reference="test-range-1",
        daily_event_range=DailyEventRange(
            start_date=start_date,
            end_date=end_date,
            daily_start_time=start_time,
            daily_end_time=end_time,
        ),
    )


def test_rules_interpreter_normalizes_explicit_daily_range():
    intent = RuleBasedIntentInterpreter(settings()).interpret(EXAMPLE, NOW.date())
    assert intent.intent is IntentType.CREATE_CALENDAR_EVENTS
    assert intent.time_constraint is None
    assert intent.title == "Houston Ballet maintenance calls"
    assert intent.daily_event_range == DailyEventRange(
        start_date=date(2026, 8, 17),
        end_date=date(2026, 8, 21),
        daily_start_time=time(9),
        daily_end_time=time(17),
    )


def test_planner_expands_inclusive_range_in_configured_timezone():
    plan = ActionPlanner(settings()).plan(range_intent(), NOW.date())
    assert len(plan.actions) == 5
    assert all(action.action is ActionType.CREATE_CALENDAR_EVENT for action in plan.actions)
    assert [action.start_iso.date() for action in plan.actions] == [
        date(2026, 8, day) for day in range(17, 22)
    ]
    for action in plan.actions:
        assert action.start_iso.timetz().replace(tzinfo=None) == time(9)
        assert action.end_iso.timetz().replace(tzinfo=None) == time(17)
        assert action.start_iso.tzinfo is ZONE
        assert action.end_iso.tzinfo is ZONE
        assert action.title == "Houston Ballet maintenance calls"
        assert action.description == "Synthetic planning test"
        assert action.calendar_name == "personal"
        assert action.source_reference == "test-range-1"


def test_single_day_uses_same_atomic_calendar_event_path():
    plan = ActionPlanner(settings()).plan(
        range_intent(end_date=date(2026, 8, 17)), NOW.date()
    )
    assert len(plan.actions) == 1
    assert plan.actions[0].action is ActionType.CREATE_CALENDAR_EVENT
    assert plan.actions[0].start_iso == datetime(2026, 8, 17, 9, tzinfo=ZONE)
    assert plan.actions[0].end_iso == datetime(2026, 8, 17, 17, tzinfo=ZONE)


def test_invalid_date_and_time_ranges_fail_model_validation():
    with pytest.raises(ValidationError, match="daily_range_end_before_start"):
        DailyEventRange(
            start_date=date(2026, 8, 18),
            end_date=date(2026, 8, 17),
            daily_start_time=time(9),
            daily_end_time=time(17),
        )
    with pytest.raises(
        ValidationError, match="daily_range_end_time_not_after_start_time"
    ):
        DailyEventRange(
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 17),
            daily_start_time=time(17),
            daily_end_time=time(17),
        )


def test_occurrence_limit_fails_before_any_execution():
    configured = settings(beacon_max_daily_range_occurrences=31)
    planner = ActionPlanner(configured)
    intent = range_intent(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 9, 1),
    )
    with pytest.raises(ValueError, match="daily_range_occurrence_limit_exceeded"):
        planner.plan(intent, NOW.date())
    scheduler = FakeCalendarScheduler()
    service = InteractionService(
        vikunja=NeverCalled(),
        scheduler=scheduler,
        daily_brief=NeverCalled(),
        settings=configured,
        clock=lambda timezone: NOW.astimezone(timezone),
    )
    with pytest.raises(ValueError, match="daily_range_occurrence_limit_exceeded"):
        service.interact(InteractRequest(intent=intent))
    assert scheduler.calls == []


class NeverCalled:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected external call: {name}")


class FakeCalendarScheduler:
    def __init__(self, fail_at: int | None = None):
        self.fail_at = fail_at
        self.calls = []

    def create_calendar_event(self, request):
        self.calls.append(request)
        if len(self.calls) == self.fail_at:
            raise CalendarEventCreationError("synthetic calendar failure")
        return CalendarEventResult(
            uid=f"event-{len(self.calls)}",
            calendar=request.calendar_name,
            title=request.title,
            start_iso=request.start_iso,
            end_iso=request.end_iso,
        )


def execute_with(scheduler):
    plan = ActionPlanner(settings()).plan(range_intent(), NOW.date())
    return ActionExecutor(
        vikunja=NeverCalled(),
        scheduler=scheduler,
        daily_brief=NeverCalled(),
    ).execute(plan, NOW, ZONE)


def test_batch_executor_calls_calendar_service_five_times():
    scheduler = FakeCalendarScheduler()
    response = execute_with(scheduler)
    assert len(scheduler.calls) == 5
    assert [call.start_iso for call in scheduler.calls] == [
        datetime(2026, 8, day, 9, tzinfo=ZONE) for day in range(17, 22)
    ]
    assert [call.end_iso for call in scheduler.calls] == [
        datetime(2026, 8, day, 17, tzinfo=ZONE) for day in range(17, 22)
    ]
    assert response.calendar_batch.status is CalendarBatchStatus.COMPLETE
    assert response.calendar_batch.action_count == 5
    assert response.calendar_batch.completed_count == 5
    assert response.calendar_batch.failed_count == 0


def test_normal_interaction_boundary_expands_example_without_task_writes():
    scheduler = FakeCalendarScheduler()
    service = InteractionService(
        vikunja=NeverCalled(),
        scheduler=scheduler,
        daily_brief=NeverCalled(),
        settings=settings(),
        clock=lambda timezone: NOW.astimezone(timezone),
    )
    response = service.interact(InteractRequest(message=EXAMPLE))
    assert response.intent.intent is IntentType.CREATE_CALENDAR_EVENTS
    assert len(response.plan.actions) == 5
    assert len(scheduler.calls) == 5
    assert response.calendar_batch.status is CalendarBatchStatus.COMPLETE


def test_partial_external_failure_is_structured_and_not_complete_success():
    scheduler = FakeCalendarScheduler(fail_at=3)
    response = execute_with(scheduler)
    assert len(scheduler.calls) == 5
    assert response.calendar_batch.status is CalendarBatchStatus.PARTIAL
    assert response.calendar_batch.completed_count == 4
    assert response.calendar_batch.failed_count == 1
    failed = response.calendar_batch.results[2]
    assert failed.status == "FAILED"
    assert failed.error_code == "calendar_event_creation_failed"
    assert response.result == "calendar_batch_partial:4/5"


class StubHttpClient:
    def __init__(self, intent):
        self.intent = intent
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(self.intent)}]}}
                ]
            },
        )


def test_gemini_output_normalizes_range_in_provider_neutral_fields():
    client = StubHttpClient(
        {
            "intent": "CREATE_CALENDAR_EVENTS",
            "title": "Houston Ballet maintenance calls",
            "daily_event_range": {
                "start_date": "2026-08-17",
                "end_date": "2026-08-21",
                "daily_start_time": "09:00:00",
                "daily_end_time": "17:00:00",
                "repeat_daily": True,
            },
        }
    )
    intent = GeminiInterpreter(
        api_key="test", model="test-model", client=client
    ).interpret(EXAMPLE)
    assert intent.intent is IntentType.CREATE_CALENDAR_EVENTS
    assert intent.daily_event_range.start_date == date(2026, 8, 17)
    assert intent.daily_event_range.end_date == date(2026, 8, 21)
    assert intent.time_constraint is None
    schema = client.calls[0][1]["json"]["generationConfig"]["responseJsonSchema"]
    assert "daily_event_range" in schema["properties"]


class RecordingCalDAV:
    def __init__(self):
        self.created = []

    def create_event(self, **kwargs):
        self.created.append(kwargs)
        return CalendarEventResult(
            uid="synthetic-event",
            calendar=kwargs["calendar_name"],
            title=kwargs["title"],
            start_iso=kwargs["start"],
            end_iso=kwargs["end"],
        )


def test_scheduler_preserves_event_metadata_at_calendar_boundary():
    caldav = RecordingCalDAV()
    scheduler = SchedulerService(caldav=caldav, settings=settings())
    action = ActionPlanner(settings()).plan(range_intent(), NOW.date()).actions[0]
    scheduler.create_calendar_event(
        CalendarEventCreateRequest(
            title=action.title,
            description=action.description,
            calendar_name=action.calendar_name,
            start_iso=action.start_iso,
            end_iso=action.end_iso,
            source_reference=action.source_reference,
        )
    )
    created = caldav.created[0]
    assert created["title"] == "Houston Ballet maintenance calls"
    assert created["calendar_name"] == "personal"
    assert "Synthetic planning test" in created["description"]
    assert "Beacon source reference: test-range-1" in created["description"]
