from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from icalendar import Event

from app.services.caldav_client import CalDAVService
from app.services.home_assistant_client import HomeAssistantClient
from app.services.vikunja_client import VikunjaClient
from app.services.waze_client import WazeClient


ZONE = ZoneInfo("America/Chicago")
START = datetime(2026, 7, 20, 0, 0, tzinfo=ZONE)
END = START + timedelta(days=1)


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = "response"

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def test_caldav_daily_events_preserve_location_and_exact_work_marker(monkeypatch):
    work = Event()
    work.add("uid", "work-1")
    work.add("summary", "Work Block — Report")
    work.add("description", "Vikunja task ID: 42")
    work.add("location", "Library")
    work.add("dtstart", START + timedelta(hours=9))
    work.add("dtend", START + timedelta(hours=10))
    unrelated = Event()
    unrelated.add("uid", "event-420")
    unrelated.add("summary", "Task 420 discussion")
    unrelated.add("description", "Vikunja task ID: 420")
    unrelated.add("dtstart", START + timedelta(hours=11))
    unrelated.add("dtend", START + timedelta(hours=12))

    class Resource:
        def __init__(self, component):
            self.icalendar_component = component

    class Calendar:
        def get_display_name(self):
            return "personal"

        def search(self, **kwargs):
            return [Resource(unrelated), Resource(work)]

    client = SimpleNamespace(
        principal=lambda: SimpleNamespace(calendars=lambda: [Calendar()])
    )
    service = CalDAVService()
    service.settings = SimpleNamespace(
        beacon_timezone="America/Chicago", calendar_names=["personal"]
    )
    monkeypatch.setattr(service, "_get_client", lambda: client)
    events = service.fetch_calendar_events(START, END)
    assert [item.uid for item in events] == ["work-1", "event-420"]
    assert events[0].location == "Library"
    assert events[0].is_beacon_work_block is True
    assert events[0].vikunja_task_id == 42
    assert events[1].vikunja_task_id == 420


def test_vikunja_list_tasks_uses_shared_normalization(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response(
            [
                {
                    "id": 7,
                    "title": "Task",
                    "description": None,
                    "due_date": "2026-07-20T18:00:00Z",
                    "priority": 4,
                    "done": False,
                }
            ]
        )

    monkeypatch.setattr("app.services.vikunja_client.httpx.get", fake_get)
    client = VikunjaClient()
    tasks = client.list_tasks()
    assert tasks[0].id == 7
    assert tasks[0].description == ""
    assert tasks[0].due_date.tzinfo is not None
    assert calls[0][1]["params"] == {"page": 1, "per_page": 100}


def test_home_assistant_weather_is_normalized(monkeypatch):
    monkeypatch.setattr(
        "app.services.home_assistant_client.httpx.get",
        lambda *args, **kwargs: Response(
            {
                "state": "partlycloudy",
                "last_updated": "2026-07-20T12:00:00Z",
                "attributes": {
                    "temperature": 78,
                    "temperature_unit": "°F",
                    "humidity": 55,
                },
            }
        ),
    )
    client = HomeAssistantClient()
    client.settings = SimpleNamespace(
        home_assistant_url="https://example.invalid",
        home_assistant_token="test",
        home_assistant_weather_entity="weather.home",
    )
    weather = client.get_weather()
    assert weather.condition == "partlycloudy"
    assert weather.temperature == 78
    assert weather.observed_at.tzinfo is not None


def test_waze_client_returns_beacon_travel_model(monkeypatch):
    class Calculator:
        def __init__(self, origin, destination, region):
            assert (origin, destination, region) == ("Home", "Theater", "US")

        def calc_route_info(self, real_time):
            assert real_time is True
            return 25.25, 12.75

    monkeypatch.setattr(
        "app.services.waze_client.WazeRouteCalculator.WazeRouteCalculator",
        Calculator,
    )
    client = WazeClient()
    client.settings = SimpleNamespace(waze_region="US")
    target = SimpleNamespace(
        uid="event-1",
        title="Rehearsal",
        start_iso=START + timedelta(hours=9),
    )
    estimate = client.estimate("Home", "Theater", target, 15)
    assert estimate.duration_minutes == 25.2
    assert estimate.distance_kilometers == 12.8
    assert estimate.leave_by == START + timedelta(hours=8, minutes=19, seconds=45)
