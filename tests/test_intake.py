import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import Settings
from app.intake.executor import ActionExecutor
from app.intake.gemini import GeminiInterpreter
from app.intake.interpreter import (
    InterpreterConfigurationError,
    InterpreterResponseError,
)
from app.intake.planner import ActionPlanner
from app.models import (
    ActionType,
    AvailabilityOption,
    IntentType,
    ScheduleStatus,
    ScheduleTaskResponse,
    StructuredIntent,
    VikunjaTask,
)


ZONE = ZoneInfo("America/Chicago")
NOW = datetime(2026, 8, 3, 8, 0, tzinfo=ZONE)


def settings() -> Settings:
    return Settings(
        beacon_api_key="test",
        nextcloud_caldav_url="https://example.invalid/caldav",
        nextcloud_username="test",
        nextcloud_app_password="test",
        vikunja_api_url="https://example.invalid/api/v1",
        vikunja_api_token="test",
        vikunja_default_project_id=7,
        beacon_calendars="personal",
        beacon_schedule_calendar="personal",
        beacon_timezone="America/Chicago",
    )


def gemini_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", "https://example.invalid"),
        json={"candidates": [{"content": {"parts": [{"text": text}]}}]},
    )


class StubHttpClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_gemini_valid_response_becomes_structured_intent():
    client = StubHttpClient(
        gemini_response(
            json.dumps(
                {
                    "intent": "SCHEDULE_TASK",
                    "title": "Lighting paperwork",
                    "time_constraint": "tomorrow",
                }
            )
        )
    )
    intent = GeminiInterpreter(
        api_key="secret", model="test-model", client=client
    ).interpret("Schedule lighting paperwork tomorrow")
    assert intent.intent is IntentType.SCHEDULE_TASK
    assert intent.title == "Lighting paperwork"
    assert intent.time_constraint == "tomorrow"
    request = client.calls[0][1]
    assert request["headers"]["x-goog-api-key"] == "secret"
    assert request["json"]["generationConfig"]["responseMimeType"] == "application/json"
    schema = request["json"]["generationConfig"]["responseJsonSchema"]
    assert "create_event" not in schema["properties"]


def test_gemini_requires_api_key():
    with pytest.raises(InterpreterConfigurationError, match="GEMINI_API_KEY"):
        GeminiInterpreter(api_key=None, model="test-model")


def test_gemini_malformed_output_fails_safely():
    interpreter = GeminiInterpreter(
        api_key="secret",
        model="test-model",
        client=StubHttpClient(gemini_response("not json")),
    )
    with pytest.raises(InterpreterResponseError, match="StructuredIntent"):
        interpreter.interpret("anything")


def test_gemini_missing_required_fields_fails_safely():
    interpreter = GeminiInterpreter(
        api_key="secret",
        model="test-model",
        client=StubHttpClient(
            gemini_response(json.dumps({"intent": "CREATE_TASK"}))
        ),
    )
    with pytest.raises(InterpreterResponseError, match="StructuredIntent"):
        interpreter.interpret("Create a task")


def test_planner_create_task_intent_has_one_create_action():
    plan = ActionPlanner(settings()).plan(
        StructuredIntent(
            intent=IntentType.CREATE_TASK,
            title="Buy Liquid IV",
            time_constraint="tomorrow",
        ),
        date(2026, 8, 3),
    )
    assert [action.action for action in plan.actions] == [ActionType.CREATE_TASK]
    assert plan.actions[0].deadline == date(2026, 8, 4)


def test_planner_schedule_task_creates_then_schedules():
    plan = ActionPlanner(settings()).plan(
        StructuredIntent(
            intent=IntentType.SCHEDULE_TASK,
            title="CAD drawings",
            time_constraint="tomorrow afternoon",
        ),
        date(2026, 8, 3),
    )
    assert [action.action for action in plan.actions] == [
        ActionType.CREATE_TASK,
        ActionType.SCHEDULE_WORK_BLOCK,
    ]
    assert plan.actions[0].reuse_existing is True
    assert plan.actions[1].deadline == date(2026, 8, 4)
    assert plan.actions[1].window_start == "12:00"
    assert plan.actions[1].window_end == "17:00"


def test_planner_ambiguous_intent_requests_clarification():
    plan = ActionPlanner(settings()).plan(
        StructuredIntent(
            intent=IntentType.UNKNOWN,
            clarification_question="Should I create a task or schedule work?",
        ),
        date(2026, 8, 3),
    )
    assert plan.actions[0].action is ActionType.REQUEST_CLARIFICATION
    assert plan.actions[0].question.startswith("Should I")


class FakeVikunja:
    def __init__(self):
        self.created = []

    def list_tasks(self):
        return []

    def create_task(self, title, due_date=None):
        self.created.append((title, due_date))
        return VikunjaTask(id=99, title=title, due_date=due_date, project_id=7)


class FakeScheduler:
    def __init__(self):
        self.calls = []

    def schedule_task(self, task, request):
        self.calls.append((task, request))
        option = AvailabilityOption(
            start_iso=request.earliest_iso,
            end_iso=request.earliest_iso + timedelta(minutes=request.duration_minutes),
            score=100,
            reasons=["test"],
        )
        return ScheduleTaskResponse(
            status=ScheduleStatus.NEW,
            task=task,
            selected_option=option,
            calendars_checked=["personal"],
            events_found=0,
        )


class UnusedBrief:
    def build(self, requested_date=None):
        raise AssertionError("brief should not be called")


def test_executor_creates_vikunja_task_and_schedules_it():
    source = FakeVikunja()
    scheduler = FakeScheduler()
    executor = ActionExecutor(
        vikunja=source,
        scheduler=scheduler,
        daily_brief=UnusedBrief(),
    )
    plan = ActionPlanner(settings()).plan(
        StructuredIntent(
            intent=IntentType.SCHEDULE_TASK,
            title="Lighting paperwork",
            time_constraint="tomorrow",
        ),
        NOW.date(),
    )
    response = executor.execute(plan, NOW, ZONE)
    assert source.created == [
        ("Lighting paperwork", datetime(2026, 8, 4, 22, 0, tzinfo=ZONE))
    ]
    assert scheduler.calls[0][0].id == 99
    assert scheduler.calls[0][1].earliest_iso == datetime(
        2026, 8, 4, 9, 0, tzinfo=ZONE
    )
    assert [item.status for item in response.actions_taken] == ["CREATED", "NEW"]
