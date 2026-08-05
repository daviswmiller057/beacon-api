from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dashboard import dashboard_service_dependency
from app.config import Settings
from app.main import app
from app.models import (
    BriefCalendarEvent,
    DashboardEventSummary,
    TodayDashboardResponse,
    VikunjaTask,
)
from app.services.daily_brief import DailyBriefService
from app.services.dashboard import TodayDashboardService


ZONE = ZoneInfo("America/Chicago")
NOW = datetime(2026, 8, 4, 8, 30, tzinfo=ZONE)


def settings() -> Settings:
    return Settings(
        beacon_api_key="test",
        nextcloud_caldav_url="https://example.invalid/caldav",
        nextcloud_username="test",
        nextcloud_app_password="test",
        vikunja_api_url="https://example.invalid/api/v1",
        vikunja_api_token="test",
        beacon_calendars="personal",
        beacon_timezone="America/Chicago",
        daily_brief_travel_enabled=False,
        daily_brief_weather_enabled=False,
    )


class FakeCalDAV:
    def __init__(self, events=None, error=None):
        self.events = list(events or [])
        self.error = error
        self.calls = []

    def fetch_calendar_events(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.events


class FakeVikunja:
    def __init__(self, tasks=None):
        self.tasks = list(tasks or [])
        self.calls = 0

    def list_tasks(self):
        self.calls += 1
        return self.tasks


def dashboard_service(*, events=None, tasks=None, calendar_error=None):
    config = settings()
    caldav = FakeCalDAV(events, calendar_error)
    vikunja = FakeVikunja(tasks)
    brief = DailyBriefService(
        caldav=caldav,
        vikunja=vikunja,
        settings=config,
        clock=lambda timezone: NOW.astimezone(timezone),
    )
    return TodayDashboardService(daily_brief=brief, settings=config), caldav, vikunja


def empty_response() -> TodayDashboardResponse:
    return TodayDashboardResponse(
        generated_at=NOW,
        timezone="America/Chicago",
        local_date=NOW.date(),
    )


class FakeDashboardService:
    def __init__(self, response=None):
        self.response = response or empty_response()
        self.calls = 0

    def build(self):
        self.calls += 1
        return self.response


@pytest.fixture
def route_service():
    service = FakeDashboardService()
    app.dependency_overrides[dashboard_service_dependency] = lambda: service
    yield service
    app.dependency_overrides.clear()


def test_dashboard_rejects_missing_authentication(route_service):
    response = TestClient(app).get("/api/v1/dashboard/today")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid Beacon API key"}
    assert route_service.calls == 0


def test_dashboard_rejects_invalid_authentication(route_service):
    response = TestClient(app).get(
        "/api/v1/dashboard/today",
        headers={"X-Beacon-API-Key": "incorrect"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid Beacon API key"}
    assert route_service.calls == 0


def test_dashboard_accepts_configured_authentication(route_service):
    response = TestClient(app).get(
        "/api/v1/dashboard/today",
        headers={"X-Beacon-API-Key": "test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert set(payload) == {
        "schema_version",
        "generated_at",
        "timezone",
        "local_date",
        "display_name",
        "next_event",
        "focus",
        "attention_items",
        "priority_tasks",
        "recommended_action",
    }
    assert route_service.calls == 1


def test_dashboard_does_not_expose_unexpected_error_details():
    class FailingDashboardService:
        def build(self):
            raise RuntimeError("private upstream response")

    service = FailingDashboardService()
    app.dependency_overrides[dashboard_service_dependency] = lambda: service
    try:
        response = TestClient(app).get(
            "/api/v1/dashboard/today",
            headers={"X-Beacon-API-Key": "test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {"detail": "Today dashboard generation failed"}


def test_dashboard_maps_supported_services_with_deterministic_local_time():
    event = BriefCalendarEvent(
        uid="calendar-event-7",
        calendar="personal",
        title="Production meeting",
        location="Main Theater",
        start_iso=datetime(2026, 8, 4, 9, 0, tzinfo=ZONE),
        end_iso=datetime(2026, 8, 4, 10, 0, tzinfo=ZONE),
    )
    tasks = [
        VikunjaTask(
            id=42,
            title="Submit lighting order",
            due_date=datetime(2026, 8, 3, 17, 0),
            priority=4,
        ),
        VikunjaTask(
            id=99,
            title="Completed item",
            priority=5,
            done=True,
        ),
    ]
    service, caldav, vikunja = dashboard_service(events=[event], tasks=tasks)

    result = service.build()

    assert result.schema_version == 1
    assert result.generated_at == NOW
    assert result.timezone == "America/Chicago"
    assert result.local_date.isoformat() == "2026-08-04"
    assert result.next_event is not None
    assert result.next_event.id == "calendar-event-7"
    assert result.next_event.calendar_name == "personal"
    assert result.next_event.leave_by_at is None
    assert [task.id for task in result.priority_tasks] == ["42"]
    assert result.priority_tasks[0].priority.value == "urgent"
    assert result.priority_tasks[0].due_at == datetime(
        2026, 8, 3, 17, 0, tzinfo=ZONE
    )
    assert result.priority_tasks[0].project_name is None
    assert result.priority_tasks[0].completed is False
    assert result.attention_items[0].id == "overdue_task:42"
    assert caldav.calls
    assert vikunja.calls == 1


def test_dashboard_unsupported_sections_are_not_fabricated():
    service, _, _ = dashboard_service()

    result = service.build()

    assert result.display_name is None
    assert result.next_event is None
    assert result.focus is None
    assert result.attention_items == []
    assert result.priority_tasks == []
    assert result.recommended_action is None


def test_dashboard_preserves_task_data_when_calendar_is_unavailable():
    service, _, _ = dashboard_service(
        tasks=[VikunjaTask(id=8, title="Call vendor", priority=3)],
        calendar_error=RuntimeError("calendar unavailable"),
    )

    result = service.build()

    assert result.next_event is None
    assert [task.id for task in result.priority_tasks] == ["8"]


def test_dashboard_serializes_provider_ids_as_stable_strings():
    task = VikunjaTask(id=123, title="Paperwork", priority=1)
    event = BriefCalendarEvent(
        uid=None,
        calendar="personal",
        title="Planning",
        start_iso=NOW + timedelta(hours=1),
        end_iso=NOW + timedelta(hours=2),
    )
    service, _, _ = dashboard_service(events=[event], tasks=[task])

    first = service.build().model_dump(mode="json")
    second = service.build().model_dump(mode="json")

    assert first["priority_tasks"][0]["id"] == "123"
    assert isinstance(first["priority_tasks"][0]["id"], str)
    assert first["next_event"]["id"].startswith("event:")
    assert first["next_event"]["id"] == second["next_event"]["id"]


def test_dashboard_event_model_rejects_invalid_or_naive_bounds():
    with pytest.raises(ValidationError):
        DashboardEventSummary(
            id="event-1",
            title="Invalid",
            start_at=datetime(2026, 8, 4, 10, 0, tzinfo=ZONE),
            end_at=datetime(2026, 8, 4, 9, 0, tzinfo=ZONE),
        )

    with pytest.raises(ValidationError):
        DashboardEventSummary(
            id="event-2",
            title="Naive",
            start_at=datetime(2026, 8, 4, 9, 0),
            end_at=datetime(2026, 8, 4, 10, 0),
        )
