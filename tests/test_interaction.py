from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import interface as interface_route
from app.config import Settings, get_settings
from app.models import (
    AvailabilityOption,
    DailyBriefCalendar,
    DailyBriefResponse,
    DailyBriefSummary,
    DailyBriefTasks,
    InteractRequest,
    IntentType,
    ScheduleStatus,
    ScheduleTaskResponse,
    StructuredIntent,
    VikunjaTask,
)
from app.services.interaction import (
    AmbiguousTaskError,
    InteractionService,
    RuleBasedIntentInterpreter,
    UnsupportedIntentError,
)
from app.main import app
from app.security import require_api_key


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
        beacon_calendars="personal",
        beacon_schedule_calendar="personal",
        beacon_timezone="America/Chicago",
        beacon_interpreter="rules",
    )


def task(task_id=42, title="Lighting paperwork") -> VikunjaTask:
    return VikunjaTask(
        id=task_id,
        title=title,
        due_date=NOW + timedelta(days=5),
    )


class FakeVikunja:
    def __init__(self, tasks):
        self.tasks = tasks
        self.requested_ids = []

    def list_tasks(self):
        return self.tasks

    def get_task(self, task_id):
        self.requested_ids.append(task_id)
        return next(item for item in self.tasks if item.id == task_id)


class FakeScheduler:
    def __init__(self):
        self.calls = []

    def schedule_task(self, source_task, request):
        self.calls.append((source_task, request))
        option = AvailabilityOption(
            start_iso=request.earliest_iso,
            end_iso=request.earliest_iso
            + timedelta(minutes=request.duration_minutes),
            score=100,
            reasons=["test"],
        )
        return ScheduleTaskResponse(
            status=ScheduleStatus.NEW,
            task=source_task,
            selected_option=option,
            calendars_checked=["personal"],
            events_found=0,
        )


class FakeBrief:
    def build(self, requested_date=None):
        target = requested_date or NOW.date()
        summary = DailyBriefSummary(
            event_count=0,
            work_block_count=0,
            overdue_task_count=0,
            due_today_task_count=0,
            conflict_count=0,
        )
        return DailyBriefResponse(
            date=target,
            timezone="America/Chicago",
            generated_at=NOW,
            calendar=DailyBriefCalendar(events=[], work_blocks=[]),
            tasks=DailyBriefTasks(overdue=[], due_today=[]),
            travel=[],
            warnings=[],
            conflicts=[],
            summary=summary,
            spoken_summary="Good morning. No schedule conflicts were found.",
        )


def interaction_service(tasks=None):
    config = settings()
    scheduler = FakeScheduler()
    service = InteractionService(
        vikunja=FakeVikunja(tasks or [task()]),
        scheduler=scheduler,
        daily_brief=FakeBrief(),
        interpreter=RuleBasedIntentInterpreter(config),
        settings=config,
        clock=lambda timezone: NOW.astimezone(timezone),
    )
    return service, scheduler


def test_rule_interpreter_parses_minimum_schedule_command():
    intent = RuleBasedIntentInterpreter(settings()).interpret(
        "Schedule lighting paperwork tomorrow", NOW.date()
    )
    assert intent.action is IntentType.SCHEDULE_TASK
    assert intent.task_title == "lighting paperwork"
    assert intent.target_date == date(2026, 8, 4)
    assert intent.duration_minutes == 60


def test_rule_interpreter_preserves_buy_as_part_of_task_title():
    intent = RuleBasedIntentInterpreter(settings()).interpret(
        "Buy Liquid IV tomorrow", NOW.date()
    )
    assert intent.action is IntentType.CREATE_TASK
    assert intent.task_title == "Buy Liquid IV"
    assert intent.target_date == date(2026, 8, 4)


def test_interaction_resolves_title_and_delegates_to_scheduler():
    service, scheduler = interaction_service()
    response = service.interact(
        InteractRequest(message="Schedule lighting paperwork tomorrow")
    )
    source_task, request = scheduler.calls[0]
    assert source_task.id == 42
    assert request.earliest_iso == datetime(2026, 8, 4, 9, 0, tzinfo=ZONE)
    assert request.deadline_iso == datetime(2026, 8, 4, 22, 0, tzinfo=ZONE)
    assert request.duration_minutes == 60
    assert response.actions_taken[0].status == "NEW"
    assert response.schedule.task.id == 42


def test_structured_intent_is_an_explicit_llm_automation_boundary():
    service, scheduler = interaction_service()
    response = service.interact(
        InteractRequest(
            intent=StructuredIntent(
                action=IntentType.SCHEDULE_TASK,
                task_id=42,
                target_date=date(2026, 8, 4),
                duration_minutes=90,
                create_event=False,
            ),
        )
    )
    assert scheduler.calls[0][1].duration_minutes == 90
    assert scheduler.calls[0][1].create_event is False
    assert response.intent.task_id == 42


def test_ambiguous_title_never_chooses_a_task():
    service, scheduler = interaction_service(
        [task(1, "Lighting paperwork"), task(2, "Lighting paperwork notes")]
    )
    with pytest.raises(AmbiguousTaskError, match="Multiple Vikunja tasks"):
        service.interact(InteractRequest(message="schedule lighting"))
    assert scheduler.calls == []


def test_task_title_containing_status_is_still_a_schedule_request():
    intent = RuleBasedIntentInterpreter(settings()).interpret(
        "schedule status report tomorrow", NOW.date()
    )
    assert intent.action is IntentType.SCHEDULE_TASK
    assert intent.task_title == "status report"


def test_schedule_parser_removes_date_preposition_from_title():
    intent = RuleBasedIntentInterpreter(settings()).interpret(
        "schedule lighting paperwork for tomorrow", NOW.date()
    )
    assert intent.task_title == "lighting paperwork"


def test_brief_interaction_is_read_only_and_returns_structured_data():
    service, scheduler = interaction_service()
    response = service.interact(InteractRequest(message="What's on tomorrow?"))
    assert response.intent.action is IntentType.BRIEF
    assert response.brief.date == date(2026, 8, 4)
    assert response.actions_taken[0].status == "READ_ONLY"
    assert scheduler.calls == []


def test_unsupported_interaction_maps_to_400(monkeypatch):
    class UnsupportedService:
        def interact(self, request):
            raise UnsupportedIntentError("unsupported")

    monkeypatch.setattr(interface_route, "InteractionService", UnsupportedService)
    with pytest.raises(HTTPException) as raised:
        interface_route.interact(InteractRequest(message="hello"))
    assert raised.value.status_code == 400


def test_status_is_secret_safe_and_reports_boundaries(monkeypatch):
    monkeypatch.setenv("CONVERSATION_ENABLED", "true")
    get_settings.cache_clear()
    try:
        response = interface_route.service_status()
        assert response.status == "ok"
        assert response.integrations["nextcloud"] is True
        assert response.interaction_modes == [
            "natural_language",
            "structured_intent",
            "conversation",
        ]
        assert "token" not in response.model_dump_json().casefold()
    finally:
        get_settings.cache_clear()


def test_interact_endpoint_is_the_authenticated_front_door(monkeypatch):
    service, _ = interaction_service()
    monkeypatch.setattr(interface_route, "InteractionService", lambda: service)
    response = TestClient(app).post(
        "/interact",
        headers={"X-Beacon-API-Key": "test"},
        json={"message": "Schedule lighting paperwork tomorrow"},
    )
    assert response.status_code == 200
    assert response.json()["actions_taken"][0]["status"] == "NEW"


def test_top_level_status_requires_api_key():
    client = TestClient(app)
    assert client.get("/status").status_code == 401
    response = client.get(
        "/status", headers={"X-Beacon-API-Key": "test"}
    )
    assert response.status_code == 200
    assert response.json()["version"] == "0.3.0"


def test_api_key_comparison_rejects_non_ascii_without_error():
    with pytest.raises(HTTPException) as raised:
        require_api_key("incorrect-🔑")
    assert raised.value.status_code == 401


def test_top_level_brief_alias_is_read_only(monkeypatch):
    monkeypatch.setattr(interface_route, "DailyBriefService", FakeBrief)
    response = TestClient(app).get(
        "/brief?date=2026-08-04",
        headers={"X-Beacon-API-Key": "test"},
    )
    assert response.status_code == 200
    assert response.json()["date"] == "2026-08-04"
