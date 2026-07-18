from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.models import (
    BriefCalendarEvent,
    BriefConflictType,
    BriefWarningSource,
    TravelEstimate,
    VikunjaTask,
    WeatherConditions,
)
from app.services.daily_brief import DailyBriefService
from app.services.home_assistant_client import HomeAssistantError
from app.services.waze_client import WazeError
from app.main import app
from app.security import require_api_key


ZONE = ZoneInfo("America/Chicago")
NOW = datetime(2026, 7, 20, 7, 0, tzinfo=ZONE)


def settings(**changes) -> Settings:
    values = {
        "beacon_api_key": "test",
        "nextcloud_caldav_url": "https://example.invalid/caldav",
        "nextcloud_username": "test",
        "nextcloud_app_password": "test",
        "vikunja_api_url": "https://example.invalid/api/v1",
        "vikunja_api_token": "test",
        "beacon_calendars": "personal,school",
        "beacon_timezone": "America/Chicago",
        "daily_brief_travel_enabled": False,
        "daily_brief_weather_enabled": False,
    }
    values.update(changes)
    return Settings(**values)


def event(
    title="Rehearsal",
    start=None,
    end=None,
    uid="event-1",
    location=None,
    work_block=False,
):
    start = start or datetime(2026, 7, 20, 9, 0, tzinfo=ZONE)
    end = end or start + timedelta(hours=1)
    return BriefCalendarEvent(
        uid=uid,
        calendar="personal",
        title=title,
        description=("Vikunja task ID: 42" if work_block else ""),
        location=location,
        start_iso=start,
        end_iso=end,
        is_beacon_work_block=work_block,
        vikunja_task_id=42 if work_block else None,
    )


def task(task_id=1, title="Paperwork", due=None, priority=0, done=False):
    return VikunjaTask(
        id=task_id,
        title=title,
        due_date=due,
        priority=priority,
        done=done,
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
    def __init__(self, tasks=None, error=None):
        self.tasks = list(tasks or [])
        self.error = error

    def list_tasks(self):
        if self.error:
            raise self.error
        return self.tasks


class FakeWaze:
    def __init__(self, minutes=30, error=None, sequential_minutes=None):
        self.minutes = minutes
        self.error = error
        self.sequential_minutes = sequential_minutes or minutes
        self.estimate_calls = []
        self.travel_calls = []

    def estimate(self, origin, destination, target_event, buffer_minutes):
        self.estimate_calls.append((origin, destination, target_event.title))
        if self.error:
            raise self.error
        return TravelEstimate(
            event_uid=target_event.uid,
            event_title=target_event.title,
            origin=origin,
            destination=destination,
            duration_minutes=self.minutes,
            distance_kilometers=10,
            buffer_minutes=buffer_minutes,
            leave_by=target_event.start_iso
            - timedelta(minutes=self.minutes + buffer_minutes),
        )

    def travel_minutes(self, origin, destination):
        self.travel_calls.append((origin, destination))
        if self.error:
            raise self.error
        return self.sequential_minutes


class FakeHomeAssistant:
    def __init__(self, weather=None, error=None):
        self.weather = weather
        self.error = error

    def get_weather(self):
        if self.error:
            raise self.error
        return self.weather


def service(
    *,
    events=None,
    tasks=None,
    config=None,
    waze=None,
    weather=None,
    calendar_error=None,
    vikunja_error=None,
):
    return DailyBriefService(
        caldav=FakeCalDAV(events, calendar_error),
        vikunja=FakeVikunja(tasks, vikunja_error),
        waze=waze,
        home_assistant=weather,
        settings=config or settings(),
        clock=lambda timezone: NOW.astimezone(timezone),
    )


def test_normal_day_builds_structured_and_spoken_summary():
    rehearsal = event(location="Theater")
    block = event(
        title="Work Block — Paperwork",
        start=datetime(2026, 7, 20, 11, 0, tzinfo=ZONE),
        end=datetime(2026, 7, 20, 12, 30, tzinfo=ZONE),
        uid="block-1",
        work_block=True,
    )
    important = task(
        due=datetime(2026, 7, 20, 20, 0, tzinfo=ZONE), priority=5
    )
    config = settings(
        daily_brief_travel_enabled=True,
        daily_brief_weather_enabled=True,
        beacon_home_location="Home",
    )
    weather = WeatherConditions(
        entity_id="weather.home", condition="sunny", temperature=80
    )
    brief = service(
        events=[block, rehearsal],
        tasks=[important],
        config=config,
        waze=FakeWaze(),
        weather=FakeHomeAssistant(weather),
    ).build()
    assert [item.title for item in brief.calendar.events] == ["Rehearsal"]
    assert brief.calendar.work_blocks[0].uid == "block-1"
    assert brief.tasks.highest_priority.id == 1
    assert brief.travel[0].leave_by == datetime(2026, 7, 20, 8, 15, tzinfo=ZONE)
    assert brief.weather.condition == "sunny"
    assert "You have Rehearsal at 9 AM." in brief.spoken_summary
    assert "Leave by 8:15 AM" in brief.spoken_summary
    assert "Beacon work block from 11 AM to 12:30 PM" in brief.spoken_summary


def test_no_events_is_valid_and_omitted_from_spoken_summary():
    brief = service().build()
    assert brief.calendar.events == []
    assert brief.calendar.work_blocks == []
    assert "You have" not in brief.spoken_summary
    assert brief.spoken_summary.endswith("No schedule conflicts were found.")


def test_no_tasks_returns_empty_task_groups():
    brief = service(events=[event()]).build()
    assert brief.tasks.overdue == []
    assert brief.tasks.due_today == []
    assert brief.tasks.highest_priority is None


def test_overdue_and_due_today_use_beacon_timezone():
    tasks = [
        task(1, "Overdue", datetime(2026, 7, 19, 23, tzinfo=ZONE)),
        task(2, "Today", datetime(2026, 7, 21, 1, tzinfo=ZoneInfo("UTC"))),
        task(3, "Done", datetime(2026, 7, 19, tzinfo=ZONE), done=True),
    ]
    brief = service(tasks=tasks).build()
    assert [item.id for item in brief.tasks.overdue] == [1]
    assert [item.id for item in brief.tasks.due_today] == [2]
    assert "1 overdue task" in brief.spoken_summary


def test_priority_selection_uses_priority_deadline_then_id():
    same_due = datetime(2026, 7, 20, 18, tzinfo=ZONE)
    tasks = [
        task(9, "Lower", same_due, priority=3),
        task(8, "Later id", same_due, priority=5),
        task(7, "Earlier id", same_due, priority=5),
        task(6, "Later deadline", same_due + timedelta(hours=1), priority=5),
    ]
    brief = service(tasks=tasks).build()
    assert brief.tasks.highest_priority.id == 7


def test_work_block_detection_is_preserved_in_response():
    brief = service(events=[event(work_block=True)]).build()
    assert brief.calendar.events == []
    assert brief.calendar.work_blocks[0].vikunja_task_id == 42


@pytest.mark.parametrize(
    ("first_work", "second_work", "expected"),
    [
        (False, False, BriefConflictType.OVERLAPPING_EVENTS),
        (True, False, BriefConflictType.WORK_BLOCK_OVERLAP),
    ],
)
def test_overlapping_events(first_work, second_work, expected):
    first = event(work_block=first_work)
    second = event(
        title="Second",
        start=datetime(2026, 7, 20, 9, 30, tzinfo=ZONE),
        end=datetime(2026, 7, 20, 10, 30, tzinfo=ZONE),
        uid="event-2",
        work_block=second_work,
    )
    brief = service(events=[first, second]).build()
    assert brief.conflicts[0].type is expected


def test_travel_calculation_and_sequential_conflict():
    first = event(location="Theater")
    second = event(
        title="Class",
        start=datetime(2026, 7, 20, 10, 15, tzinfo=ZONE),
        end=datetime(2026, 7, 20, 11, 0, tzinfo=ZONE),
        uid="event-2",
        location="School",
    )
    brief = service(
        events=[first, second],
        config=settings(
            daily_brief_travel_enabled=True,
            beacon_home_location="Home",
        ),
        waze=FakeWaze(sequential_minutes=30),
    ).build()
    assert len(brief.travel) == 2
    assert any(
        conflict.type is BriefConflictType.INSUFFICIENT_TRAVEL_TIME
        for conflict in brief.conflicts
    )


def test_sequential_travel_does_not_skip_intervening_event_without_location():
    first = event(location="Theater")
    middle = event(
        title="Online meeting",
        start=datetime(2026, 7, 20, 10, 15, tzinfo=ZONE),
        end=datetime(2026, 7, 20, 10, 45, tzinfo=ZONE),
        uid="event-2",
    )
    last = event(
        title="Class",
        start=datetime(2026, 7, 20, 11, 0, tzinfo=ZONE),
        end=datetime(2026, 7, 20, 12, 0, tzinfo=ZONE),
        uid="event-3",
        location="School",
    )
    waze = FakeWaze(sequential_minutes=90)
    brief = service(
        events=[first, middle, last],
        config=settings(
            daily_brief_travel_enabled=True,
            beacon_home_location="Home",
        ),
        waze=waze,
    ).build()
    assert waze.travel_calls == []
    assert not any(
        conflict.type is BriefConflictType.INSUFFICIENT_TRAVEL_TIME
        for conflict in brief.conflicts
    )


def test_travel_failure_is_warning_not_brief_failure():
    brief = service(
        events=[event(location="Theater")],
        config=settings(
            daily_brief_travel_enabled=True,
            beacon_home_location="Home",
        ),
        waze=FakeWaze(error=WazeError("traffic unavailable")),
    ).build()
    assert brief.travel == []
    assert brief.warnings[0].source is BriefWarningSource.WAZE


def test_leave_by_already_passed_is_conflict():
    brief = service(
        events=[
            event(
                start=datetime(2026, 7, 20, 7, 30, tzinfo=ZONE),
                location="Theater",
            )
        ],
        config=settings(
            daily_brief_travel_enabled=True,
            beacon_home_location="Home",
        ),
        waze=FakeWaze(minutes=20),
    ).build()
    assert any(
        conflict.type is BriefConflictType.LEAVE_BY_PASSED
        for conflict in brief.conflicts
    )


def test_weather_unavailable_is_warning():
    brief = service(
        config=settings(daily_brief_weather_enabled=True),
        weather=FakeHomeAssistant(error=HomeAssistantError("HA down")),
    ).build()
    assert brief.weather is None
    assert brief.warnings[0].code == "WEATHER_UNAVAILABLE"


def test_timezone_and_date_override_define_calendar_bounds():
    caldav = FakeCalDAV()
    brief_service = DailyBriefService(
        caldav=caldav,
        vikunja=FakeVikunja(),
        settings=settings(),
        clock=lambda timezone: NOW.astimezone(timezone),
    )
    override = date(2026, 7, 25)
    brief = brief_service.build(override)
    start, end = caldav.calls[0][0]
    assert brief.date == override
    assert brief.timezone == "America/Chicago"
    assert start == datetime(2026, 7, 25, 0, 0, tzinfo=ZONE)
    assert end == datetime(2026, 7, 26, 0, 0, tzinfo=ZONE)


def test_spoken_summary_naturally_omits_empty_sections():
    brief = service(tasks=[task(priority=4)]).build()
    assert brief.spoken_summary == (
        "Good morning. Your highest priority is Paperwork. "
        "No schedule conflicts were found."
    )


def test_calendar_failure_returns_warning_and_keeps_tasks():
    brief = service(
        tasks=[task(priority=2)], calendar_error=RuntimeError("CalDAV down")
    ).build()
    assert brief.tasks.highest_priority.id == 1
    assert brief.warnings[0].source is BriefWarningSource.CALENDAR


def test_vikunja_failure_returns_warning_and_keeps_calendar():
    brief = service(
        events=[event()], vikunja_error=RuntimeError("Vikunja down")
    ).build()
    assert brief.calendar.events[0].title == "Rehearsal"
    assert brief.warnings[0].source is BriefWarningSource.VIKUNJA


def test_daily_brief_endpoint_accepts_date_override(monkeypatch):
    override = date(2026, 7, 25)
    expected = service().build(override)

    class FakeDailyBriefService:
        def build(self, requested_date=None):
            assert requested_date == override
            return expected

    monkeypatch.setattr(
        "app.api.daily_brief.DailyBriefService", FakeDailyBriefService
    )
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = TestClient(app).get(
            "/v1/brief/daily?date=2026-07-25"
        )
        assert response.status_code == 200
        assert response.json()["date"] == "2026-07-25"
    finally:
        app.dependency_overrides.clear()
