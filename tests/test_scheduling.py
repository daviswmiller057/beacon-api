from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from icalendar import Alarm, Event

from app.api import scheduling as scheduling_route
from app.config import Settings
from app.models import (
    BusyInterval,
    CalendarEventResult,
    ScheduleStatus,
    ScheduleTaskRequest,
    VikunjaTask,
)
from app.services.caldav_client import (
    CalDAVService,
    CalendarEventMatch,
    CalendarEventNotFoundError,
    CalendarEventUpdateError,
)
from app.services.scheduler import (
    MissingDeadlineError,
    MultipleTaskEventsError,
    NoAvailabilityError,
    SchedulerService,
    TaskAlreadyCompletedError,
)
from app.services.vikunja_client import VikunjaTaskNotFound


CHICAGO = ZoneInfo("America/Chicago")
EARLIEST = datetime(2026, 7, 20, 9, 0, tzinfo=CHICAGO)
DEADLINE = datetime(2026, 7, 20, 17, 0, tzinfo=CHICAGO)


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
    )


def task(**changes) -> VikunjaTask:
    values = {
        "id": 42,
        "title": "Prepare report",
        "due_date": DEADLINE,
    }
    values.update(changes)
    return VikunjaTask(**values)


def request(**changes) -> ScheduleTaskRequest:
    values = {
        "duration_minutes": 60,
        "earliest_iso": EARLIEST,
        "deadline_iso": DEADLINE,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
    }
    values.update(changes)
    return ScheduleTaskRequest(**values)


def match(
    start: datetime = EARLIEST,
    end: datetime = EARLIEST + timedelta(hours=1),
    uid: str = "uid-42",
) -> CalendarEventMatch:
    result = CalendarEventResult(
        uid=uid,
        href="https://example.invalid/event.ics",
        calendar="personal",
        title="Work Block — Prepare report",
        start_iso=start,
        end_iso=end,
    )
    return CalendarEventMatch(
        result=result,
        description="Scheduled by Beacon\n\nVikunja task ID: 42",
        resource=object(),
    )


class FakeCalDAV:
    def __init__(self, matches=None, busy=None, update_error=None):
        self.matches = list(matches or [])
        self.busy = list(busy or [])
        self.update_error = update_error
        self.created = []
        self.updated = []
        self.excluded_task_ids = []
        self.find_calls = []

    def find_task_events(self, **kwargs):
        self.find_calls.append(kwargs)
        return self.matches

    def fetch_busy_intervals(self, **kwargs):
        self.excluded_task_ids.append(kwargs.get("exclude_task_id"))
        return self.busy

    def create_event(self, **kwargs):
        self.created.append(kwargs)
        return CalendarEventResult(
            uid="new-uid",
            href="https://example.invalid/new.ics",
            calendar=kwargs["calendar_name"],
            title=kwargs["title"],
            start_iso=kwargs["start"],
            end_iso=kwargs["end"],
        )

    def update_event(self, **kwargs):
        if self.update_error:
            raise self.update_error
        self.updated.append(kwargs)
        existing = kwargs["match"].result
        return existing.model_copy(
            update={
                "start_iso": kwargs["start"],
                "end_iso": kwargs["end"],
            }
        )


def scheduler(caldav: FakeCalDAV) -> SchedulerService:
    return SchedulerService(caldav=caldav, settings=settings())


def test_new_event_is_created_with_marker():
    caldav = FakeCalDAV()
    response = scheduler(caldav).schedule_task(task(), request())
    assert response.status is ScheduleStatus.NEW
    assert response.already_scheduled is False
    assert response.calendar_event.uid == "new-uid"
    assert "Vikunja task ID: 42" in caldav.created[0]["description"]
    assert caldav.updated == []


def test_duplicate_is_detected_and_unchanged_without_write():
    existing = match()
    caldav = FakeCalDAV(matches=[existing])
    response = scheduler(caldav).schedule_task(task(), request())
    assert response.status is ScheduleStatus.UNCHANGED
    assert response.already_scheduled is True
    assert response.calendar_event.uid == existing.result.uid
    assert caldav.excluded_task_ids == [42]
    assert caldav.created == []
    assert caldav.updated == []


def test_existing_event_is_updated_in_place_when_slot_changes():
    existing = match(
        start=EARLIEST + timedelta(hours=2),
        end=EARLIEST + timedelta(hours=3),
    )
    caldav = FakeCalDAV(matches=[existing])
    response = scheduler(caldav).schedule_task(task(), request())
    assert response.status is ScheduleStatus.UPDATED
    assert response.calendar_event.uid == existing.result.uid
    assert response.calendar_event.start_iso == EARLIEST
    assert len(caldav.updated) == 1
    assert caldav.created == []


def test_recommendation_mode_never_writes():
    existing = match(start=EARLIEST + timedelta(hours=2))
    caldav = FakeCalDAV(matches=[existing])
    response = scheduler(caldav).schedule_task(
        task(), request(create_event=False)
    )
    assert response.status is ScheduleStatus.RECOMMENDATION_ONLY
    assert response.calendar_event.uid == existing.result.uid
    assert caldav.created == []
    assert caldav.updated == []


def test_completed_task_is_rejected_before_calendar_lookup():
    caldav = FakeCalDAV()
    with pytest.raises(TaskAlreadyCompletedError):
        scheduler(caldav).schedule_task(task(done=True), request())
    assert caldav.find_calls == []


def test_missing_deadline_is_rejected():
    caldav = FakeCalDAV()
    with pytest.raises(MissingDeadlineError):
        scheduler(caldav).schedule_task(
            task(due_date=None),
            ScheduleTaskRequest(
                duration_minutes=60,
                earliest_iso=EARLIEST,
            ),
        )


def test_no_availability_is_reported():
    busy = BusyInterval(
        start_iso=EARLIEST,
        end_iso=DEADLINE,
        calendar="personal",
    )
    with pytest.raises(NoAvailabilityError):
        scheduler(FakeCalDAV(busy=[busy])).schedule_task(task(), request())


def test_update_failure_is_not_hidden():
    error = CalendarEventUpdateError("CalDAV save failed")
    caldav = FakeCalDAV(
        matches=[match(start=EARLIEST + timedelta(hours=2))],
        update_error=error,
    )
    with pytest.raises(CalendarEventUpdateError, match="CalDAV save failed"):
        scheduler(caldav).schedule_task(task(), request())


def test_multiple_matches_are_rejected_before_availability():
    caldav = FakeCalDAV(matches=[match(), match(uid="uid-duplicate")])
    with pytest.raises(MultipleTaskEventsError):
        scheduler(caldav).schedule_task(task(), request())
    assert caldav.excluded_task_ids == []


def test_equal_instants_in_different_timezones_are_unchanged():
    utc_start = EARLIEST.astimezone(timezone.utc)
    existing = match(
        start=utc_start,
        end=utc_start + timedelta(hours=1),
    )
    caldav = FakeCalDAV(matches=[existing])
    response = scheduler(caldav).schedule_task(task(), request())
    assert response.status is ScheduleStatus.UNCHANGED
    assert caldav.updated == []


class FakeResource:
    def __init__(self, component: Event):
        self.icalendar_component = component
        self.url = "https://example.invalid/event.ics"
        self.saved_with = None

    def load(self):
        return self

    def save(self, **kwargs):
        self.saved_with = kwargs
        return self


def test_caldav_update_preserves_uid_description_and_resource():
    component = Event()
    component.add("uid", "uid-42")
    component.add("summary", "Original title")
    component.add("description", "Vikunja task ID: 42\nKeep this text")
    component.add("x-beacon-custom", "keep-custom-property")
    component.add("dtstart", EARLIEST + timedelta(hours=2))
    component.add("dtend", EARLIEST + timedelta(hours=3))
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", "Keep this alarm")
    alarm.add("trigger", timedelta(minutes=-15))
    component.add_component(alarm)
    resource = FakeResource(component)
    existing = match(
        start=EARLIEST + timedelta(hours=2),
        end=EARLIEST + timedelta(hours=3),
    )
    existing.resource = resource
    service = CalDAVService()
    service.settings = SimpleNamespace(beacon_timezone="America/Chicago")
    result = service.update_event(
        existing, 42, EARLIEST, EARLIEST + timedelta(hours=1)
    )
    assert result.uid == "uid-42"
    assert component.get("UID") == "uid-42"
    assert result.href == "https://example.invalid/event.ics"
    assert str(component.get("SUMMARY")) == "Original title"
    assert "Keep this text" in str(component.get("DESCRIPTION"))
    assert str(component.get("X-BEACON-CUSTOM")) == "keep-custom-property"
    assert len([item for item in component.subcomponents if item.name == "VALARM"]) == 1
    assert resource.saved_with == {
        "no_create": True,
        "increase_seqno": False,
    }


def test_busy_intervals_exclude_current_task_marker(monkeypatch):
    own_component = Event()
    own_component.add("description", "Vikunja task ID: 42")
    own_component.add("summary", "Current work block")
    own_component.add("dtstart", EARLIEST)
    own_component.add("dtend", EARLIEST + timedelta(hours=1))
    other_component = Event()
    other_component.add("description", "Unrelated event")
    other_component.add("summary", "Meeting")
    other_component.add("dtstart", EARLIEST + timedelta(hours=2))
    other_component.add("dtend", EARLIEST + timedelta(hours=3))

    class SearchResource:
        def __init__(self, component):
            self.icalendar_component = component

    class Calendar:
        def get_display_name(self):
            return "personal"

        def search(self, **kwargs):
            return [SearchResource(own_component), SearchResource(other_component)]

    class Principal:
        def calendars(self):
            return [Calendar()]

    class Client:
        def principal(self):
            return Principal()

    service = CalDAVService()
    service.settings = SimpleNamespace(
        beacon_timezone="America/Chicago",
        calendar_names=["personal"],
    )
    monkeypatch.setattr(service, "_get_client", lambda: Client())
    intervals = service.fetch_busy_intervals(
        EARLIEST,
        DEADLINE,
        calendar_names=["personal"],
        exclude_task_id=42,
    )
    assert len(intervals) == 1
    assert intervals[0].title == "Meeting"


def test_task_marker_does_not_match_another_task_id(monkeypatch):
    component = Event()
    component.add("description", "Vikunja task ID: 420")
    component.add("summary", "Task 420")
    component.add("dtstart", EARLIEST)
    component.add("dtend", EARLIEST + timedelta(hours=1))

    class SearchResource:
        icalendar_component = component

    class Calendar:
        def get_display_name(self):
            return "personal"

        def search(self, **kwargs):
            return [SearchResource()]

    class Client:
        def principal(self):
            return SimpleNamespace(calendars=lambda: [Calendar()])

    service = CalDAVService()
    service.settings = SimpleNamespace(
        beacon_timezone="America/Chicago",
        calendar_names=["personal"],
    )
    monkeypatch.setattr(service, "_get_client", lambda: Client())
    intervals = service.fetch_busy_intervals(
        EARLIEST,
        DEADLINE,
        calendar_names=["personal"],
        exclude_task_id=42,
    )
    matches = service.find_task_events(
        "personal", 42, EARLIEST, DEADLINE
    )
    assert len(intervals) == 1
    assert matches == []


def test_duration_based_event_is_rejected_without_save():
    component = Event()
    component.add("uid", "uid-42")
    component.add("description", "Vikunja task ID: 42")
    component.add("dtstart", EARLIEST)
    component.add("duration", timedelta(hours=1))
    resource = FakeResource(component)
    existing = match()
    existing.resource = resource
    service = CalDAVService()
    service.settings = SimpleNamespace(beacon_timezone="America/Chicago")
    with pytest.raises(CalendarEventUpdateError, match="unsupported duration"):
        service.update_event(
            existing, 42, EARLIEST + timedelta(hours=1), EARLIEST + timedelta(hours=2)
        )
    assert resource.saved_with is None


def test_missing_task_maps_to_404(monkeypatch):
    class MissingVikunja:
        def get_task(self, task_id):
            raise VikunjaTaskNotFound(f"Vikunja task {task_id} was not found")

    monkeypatch.setattr(scheduling_route, "VikunjaClient", MissingVikunja)
    with pytest.raises(HTTPException) as raised:
        scheduling_route.schedule_task(42, request())
    assert raised.value.status_code == 404


def test_update_failure_maps_to_502(monkeypatch):
    class FakeVikunja:
        def get_task(self, task_id):
            return task()

    class FailingScheduler:
        def schedule_task(self, source_task, source_request):
            raise CalendarEventUpdateError("CalDAV save failed")

    monkeypatch.setattr(scheduling_route, "VikunjaClient", FakeVikunja)
    monkeypatch.setattr(scheduling_route, "SchedulerService", FailingScheduler)
    with pytest.raises(HTTPException) as raised:
        scheduling_route.schedule_task(42, request())
    assert raised.value.status_code == 502
    assert raised.value.detail == "CalDAV save failed"


def test_missing_calendar_event_maps_to_404(monkeypatch):
    class FakeVikunja:
        def get_task(self, task_id):
            return task()

    class StaleScheduler:
        def schedule_task(self, source_task, source_request):
            raise CalendarEventNotFoundError("Beacon event is stale")

    monkeypatch.setattr(scheduling_route, "VikunjaClient", FakeVikunja)
    monkeypatch.setattr(scheduling_route, "SchedulerService", StaleScheduler)
    with pytest.raises(HTTPException) as raised:
        scheduling_route.schedule_task(42, request())
    assert raised.value.status_code == 404
    assert raised.value.detail == "Beacon event is stale"
