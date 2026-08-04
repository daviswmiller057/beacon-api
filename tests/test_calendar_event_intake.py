import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from icalendar import Event

from app.api import interface as interface_route
from app.config import Settings
from app.intake.executor import ActionExecutor
from app.intake.gemini import GeminiInterpreter
from app.intake.planner import ActionPlanner
from app.intake.rules import RuleBasedIntentInterpreter
from app.main import app
from app.models import (
    ActionType,
    BriefCalendarEvent,
    CalendarCategory,
    CalendarEventCreateStatus,
    CalendarEventResult,
    IntentType,
    InteractRequest,
    LocationCandidate,
    LocationResolution,
    LocationResolutionStatus,
    StructuredIntent,
)
from app.services.caldav_client import CalDAVService
from app.services.calendar_events import (
    CalendarEventRoutingError,
    CalendarEventService,
    CalendarEventValidationError,
)
from app.services.interaction import InteractionService


ZONE = ZoneInfo("America/Chicago")
NOW = datetime(2026, 8, 3, 8, 0, tzinfo=ZONE)
START = datetime(2026, 8, 10, 10, 0, tzinfo=ZONE)
END = datetime(2026, 8, 10, 18, 0, tzinfo=ZONE)
MESSAGE = "AD Players focus call for Holly Street on Monday 8/10 from 10:00-18:00"
START_ONLY_MESSAGE = "Dr Morland Aug 4th at 14:00"
START_ONLY_START = datetime(2026, 8, 4, 14, 0, tzinfo=ZONE)
START_ONLY_END = datetime(2026, 8, 4, 15, 0, tzinfo=ZONE)


def settings() -> Settings:
    return Settings(
        beacon_api_key="test",
        nextcloud_caldav_url="https://example.invalid/caldav",
        nextcloud_username="test",
        nextcloud_app_password="test",
        vikunja_api_url="https://example.invalid/api/v1",
        vikunja_api_token="test",
        beacon_calendars="theater,school,personal",
        beacon_schedule_calendar="personal",
        beacon_timezone="America/Chicago",
        beacon_interpreter="rules",
        beacon_location_lookup_enabled=True,
        beacon_location_bias="Houston, TX",
    )


class FakeCalDAV:
    def __init__(self, conflicts=None):
        self.conflicts = list(conflicts or [])
        self.events = []
        self.created = []
        self.find_calls = []
        self.fetch_calls = []

    def find_fixed_events(self, **kwargs):
        self.find_calls.append(kwargs)
        normalized = " ".join(kwargs["title"].casefold().split())
        return [
            event
            for event in self.events
            if event.calendar.casefold() == kwargs["calendar_name"].casefold()
            and " ".join(event.title.casefold().split()) == normalized
            and event.start_iso == kwargs["start"]
            and event.end_iso == kwargs["end"]
        ]

    def fetch_calendar_events(self, *args, **kwargs):
        self.fetch_calls.append((args, kwargs))
        return self.conflicts

    def create_event(self, **kwargs):
        self.created.append(kwargs)
        event = CalendarEventResult(
            uid=f"fixed-{len(self.created)}",
            href="https://example.invalid/fixed.ics",
            calendar=kwargs["calendar_name"],
            title=kwargs["title"],
            start_iso=kwargs["start"],
            end_iso=kwargs["end"],
            location=kwargs.get("location"),
        )
        self.events.append(event)
        return event


class ForbiddenVikunja:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def forbidden(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"Vikunja.{name} must not be called")

        return forbidden


class ForbiddenScheduler:
    def __init__(self):
        self.calls = []

    def schedule_task(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("work-block scheduling must not be called")


class ForbiddenBrief:
    def build(self, *args, **kwargs):
        raise AssertionError("Daily Brief must not be called")


class FakeResolver:
    def __init__(self, resolution=None):
        self.calls = []
        self.resolution = resolution

    def resolve(self, query, *, geographic_bias=None):
        self.calls.append((query, geographic_bias))
        return self.resolution or LocationResolution(
            query=query,
            status=LocationResolutionStatus.RESOLVED,
            provider="fake",
            selected=LocationCandidate(
                canonical_name="A.D. Players at the George Theater",
                formatted_address=(
                    "5420 Westheimer Road, Houston, TX 77056"
                ),
                latitude=29.739,
                longitude=-95.472,
                provider_id="fake:ad-players",
                confidence=0.98,
                matching_evidence=["exact_name", "geographic_bias:1.00"],
            ),
        )


def event_service(caldav=None, resolver=None, custom_settings=None):
    return CalendarEventService(
        caldav=caldav or FakeCalDAV(),
        location_resolver=resolver or FakeResolver(),
        settings=custom_settings or settings(),
    )


def acceptance_intent() -> StructuredIntent:
    return RuleBasedIntentInterpreter(settings()).interpret(
        MESSAGE,
        date(2026, 8, 3),
    )


def test_acceptance_sentence_becomes_fixed_calendar_event():
    intent = acceptance_intent()

    assert intent.intent is IntentType.CREATE_CALENDAR_EVENT
    assert intent.title == "Focus call for Holly Street"
    assert intent.location_query == "AD Players"
    assert intent.calendar_category is CalendarCategory.THEATER
    assert intent.start_iso == START
    assert intent.end_iso == END
    assert intent.start_iso.utcoffset().total_seconds() == -5 * 3600


@pytest.mark.parametrize(
    ("message", "expected_start", "expected_end"),
    [
        (
            "Rehearsal Tuesday from 7pm to 10pm",
            datetime(2026, 8, 4, 19, 0, tzinfo=ZONE),
            datetime(2026, 8, 4, 22, 0, tzinfo=ZONE),
        ),
        (
            "Dentist appointment August 14 at 2pm until 3pm",
            datetime(2026, 8, 14, 14, 0, tzinfo=ZONE),
            datetime(2026, 8, 14, 15, 0, tzinfo=ZONE),
        ),
        (
            "Class on Wednesday from 9:00 to 10:30",
            datetime(2026, 8, 5, 9, 0, tzinfo=ZONE),
            datetime(2026, 8, 5, 10, 30, tzinfo=ZONE),
        ),
    ],
)
def test_rules_parse_supported_fixed_event_forms(
    message,
    expected_start,
    expected_end,
):
    intent = RuleBasedIntentInterpreter(settings()).interpret(
        message,
        date(2026, 8, 3),
    )

    assert intent.intent is IntentType.CREATE_CALENDAR_EVENT
    assert intent.start_iso == expected_start
    assert intent.end_iso == expected_end


def test_rules_extract_explicit_venue_without_removing_subject():
    intent = RuleBasedIntentInterpreter(settings()).interpret(
        "Carmen rehearsal at Moores Opera House Tuesday from 7pm to 10pm",
        date(2026, 8, 3),
    )

    assert intent.title == "Carmen rehearsal"
    assert intent.location_query == "Moores Opera House"
    assert intent.calendar_category is CalendarCategory.THEATER


def test_rules_move_logistical_note_into_description():
    intent = RuleBasedIntentInterpreter(settings()).interpret(
        "Load-in at Miller Outdoor Theatre Friday from 8am to 4pm, "
        "use the stage door",
        date(2026, 8, 3),
    )

    assert intent.title == "Load-in"
    assert intent.location_query == "Miller Outdoor Theatre"
    assert intent.description == "Use the stage door"

    caldav = FakeCalDAV()
    resolver = FakeResolver(
        LocationResolution(
            query="Miller Outdoor Theatre",
            status=LocationResolutionStatus.NOT_FOUND,
            provider="fake",
        )
    )
    event_service(caldav, resolver).create_fixed_event(
        title=intent.title,
        start=intent.start_iso,
        end=intent.end_iso,
        calendar_category=intent.calendar_category,
        location_query=intent.location_query,
        description=intent.description,
    )
    assert caldav.created[0]["description"] == "Use the stage door"


def test_virtual_location_is_canonical_without_physical_lookup():
    resolver = FakeResolver()
    caldav = FakeCalDAV()
    intent = RuleBasedIntentInterpreter(settings()).interpret(
        "Zoom meeting with Nate Wednesday from 2pm to 3pm",
        date(2026, 8, 3),
    )
    executor = ActionExecutor(
        vikunja=ForbiddenVikunja(),
        scheduler=ForbiddenScheduler(),
        daily_brief=ForbiddenBrief(),
        calendar_events=event_service(caldav, resolver),
    )

    response = executor.execute(
        ActionPlanner(settings()).plan(intent, NOW.date()),
        NOW,
        ZONE,
    )

    assert intent.title == "Meeting with Nate"
    assert intent.location_query == "Zoom"
    assert resolver.calls == []
    assert caldav.created[0]["location"] == "Zoom"
    assert response.calendar_event.location_resolution.status is (
        LocationResolutionStatus.SKIPPED
    )

    explicit = RuleBasedIntentInterpreter(settings()).interpret(
        "Meeting with Nate on Zoom Wednesday from 2pm to 3pm",
        date(2026, 8, 3),
    )
    assert explicit.title == "Meeting with Nate"
    assert explicit.location_query == "Zoom"


def test_planner_authorizes_only_calendar_event_creation():
    plan = ActionPlanner(settings()).plan(acceptance_intent(), NOW.date())

    assert [action.action for action in plan.actions] == [
        ActionType.CREATE_CALENDAR_EVENT
    ]
    assert plan.actions[0].start_iso == START
    assert plan.actions[0].end_iso == END
    assert plan.actions[0].location_query == "AD Players"


def test_acceptance_example_routes_to_theater():
    service = event_service()

    assert (
        service.route_calendar(
            title=acceptance_intent().title,
            location=acceptance_intent().location_query,
        )
        == "theater"
    )
    assert service.route_calendar(title="Class lecture") == "school"
    assert service.route_calendar(title="Dentist appointment") == "personal"


def test_routing_normalizes_configured_names_and_rejects_missing_destination():
    normalized = settings().model_copy(
        update={"beacon_calendars": " Theater , SCHOOL , personal "}
    )
    service = CalendarEventService(caldav=FakeCalDAV(), settings=normalized)

    assert service.route_calendar(title="AD Players rehearsal") == "Theater"
    assert (
        service.route_calendar(
            title="Department meeting",
            calendar_category=CalendarCategory.SCHOOL,
        )
        == "SCHOOL"
    )

    unavailable = settings().model_copy(
        update={"beacon_calendars": "school,personal"}
    )
    with pytest.raises(CalendarEventRoutingError, match="not configured"):
        CalendarEventService(
            caldav=FakeCalDAV(),
            settings=unavailable,
        ).route_calendar(title="AD Players rehearsal")


def test_optional_fixed_event_metadata_reaches_shared_caldav_adapter():
    caldav = FakeCalDAV()

    event_service(caldav).create_fixed_event(
        title="Dentist appointment",
        start=START,
        end=END,
        location="Clinic",
        description="Routine cleaning",
    )

    assert caldav.created[0]["calendar_name"] == "personal"
    assert caldav.created[0]["location"] == "Clinic"
    assert caldav.created[0]["description"] == "Routine cleaning"


def test_executor_creates_one_normal_event_without_task_or_scheduler_calls():
    caldav = FakeCalDAV()
    resolver = FakeResolver()
    vikunja = ForbiddenVikunja()
    scheduler = ForbiddenScheduler()
    executor = ActionExecutor(
        vikunja=vikunja,
        scheduler=scheduler,
        daily_brief=ForbiddenBrief(),
        calendar_events=event_service(caldav, resolver),
    )

    response = executor.execute(
        ActionPlanner(settings()).plan(acceptance_intent(), NOW.date()),
        NOW,
        ZONE,
    )

    assert response.calendar_event.status is CalendarEventCreateStatus.CREATED
    assert response.calendar_event.event.calendar == "theater"
    assert response.calendar_event.event.title == "Focus call for Holly Street"
    assert response.calendar_event.event.location == (
        "A.D. Players at the George Theater, "
        "5420 Westheimer Road, Houston, TX 77056"
    )
    assert response.calendar_event.location_resolution.selected.latitude == 29.739
    assert len(caldav.created) == 1
    assert resolver.calls == [("AD Players", "Houston, TX")]
    assert "Vikunja task ID" not in caldav.created[0]["description"]
    assert vikunja.calls == []
    assert scheduler.calls == []
    assert response.task is None
    assert response.schedule is None
    assert response.result == (
        'Created calendar event "Focus call for Holly Street" on '
        "Monday, August 10, 2026 from 10:00 AM to 6:00 PM in Theater at "
        "A.D. Players at the George Theater, 5420 Westheimer Road, "
        "Houston, TX 77056."
    )


def test_repeated_execution_returns_existing_event_without_duplicate():
    caldav = FakeCalDAV()
    resolver = FakeResolver()
    executor = ActionExecutor(
        vikunja=ForbiddenVikunja(),
        scheduler=ForbiddenScheduler(),
        daily_brief=ForbiddenBrief(),
        calendar_events=event_service(caldav, resolver),
    )
    plan = ActionPlanner(settings()).plan(acceptance_intent(), NOW.date())

    first = executor.execute(plan, NOW, ZONE)
    second = executor.execute(plan, NOW, ZONE)

    assert first.calendar_event.status is CalendarEventCreateStatus.CREATED
    assert second.calendar_event.status is CalendarEventCreateStatus.EXISTING
    assert len(caldav.created) == 1
    assert len(resolver.calls) == 1
    assert second.calendar_event.event.title == "Focus call for Holly Street"
    assert second.calendar_event.event.location.startswith("A.D. Players")
    assert second.result.startswith("Calendar event already exists:")


def test_ambiguous_location_requests_clarification_without_creation():
    candidates = [
        LocationCandidate(
            canonical_name="St. Luke's United Methodist Church",
            formatted_address="3471 Westheimer Road, Houston, TX",
            confidence=0.91,
        ),
        LocationCandidate(
            canonical_name="St. Luke the Evangelist",
            formatted_address="11011 Hall Road, Houston, TX",
            confidence=0.89,
        ),
    ]
    resolver = FakeResolver(
        LocationResolution(
            query="St. Luke's",
            status=LocationResolutionStatus.AMBIGUOUS,
            provider="fake",
            alternatives=candidates,
        )
    )
    caldav = FakeCalDAV()
    service = event_service(caldav, resolver)

    response = service.create_fixed_event(
        title="Choir rehearsal",
        start=START,
        end=END,
        location_query="St. Luke's",
    )

    assert response.status is CalendarEventCreateStatus.CLARIFICATION
    assert "I found multiple matches" in response.clarification_question
    assert "3471 Westheimer Road" in response.clarification_question
    assert caldav.created == []
    assert caldav.fetch_calls == []

    executor = ActionExecutor(
        vikunja=ForbiddenVikunja(),
        scheduler=ForbiddenScheduler(),
        daily_brief=ForbiddenBrief(),
        calendar_events=service,
    )
    intent = StructuredIntent(
        intent=IntentType.CREATE_CALENDAR_EVENT,
        title="Choir rehearsal",
        start_iso=START,
        end_iso=END,
        location_query="St. Luke's",
    )
    interaction = executor.execute(
        ActionPlanner(settings()).plan(intent, NOW.date()),
        NOW,
        ZONE,
    )
    assert interaction.result.startswith("I found multiple matches")
    assert interaction.actions_taken[0].status == "PENDING"
    assert caldav.created == []


@pytest.mark.parametrize(
    ("status", "warning"),
    [
        (LocationResolutionStatus.NOT_FOUND, "could not be verified"),
        (LocationResolutionStatus.UNAVAILABLE, "resolution was unavailable"),
    ],
)
def test_unresolved_location_uses_raw_query_with_warning(status, warning):
    resolver = FakeResolver(
        LocationResolution(
            query="Unknown Hall",
            status=status,
            provider="fake",
            detail="provider timeout" if status is LocationResolutionStatus.UNAVAILABLE else None,
        )
    )
    caldav = FakeCalDAV()

    response = event_service(caldav, resolver).create_fixed_event(
        title="Rehearsal",
        start=START,
        end=END,
        location_query="Unknown Hall",
    )

    assert response.status is CalendarEventCreateStatus.CREATED
    assert caldav.created[0]["location"] == "Unknown Hall"
    assert warning in response.warnings[0]
    assert warning in ActionExecutor._calendar_event_result(response, ZONE)


def test_event_without_location_does_not_call_resolver():
    resolver = FakeResolver()
    caldav = FakeCalDAV()

    event_service(caldav, resolver).create_fixed_event(
        title="Rehearsal",
        start=START,
        end=END,
    )

    assert resolver.calls == []
    assert caldav.created[0]["location"] is None


def test_start_without_end_or_duration_defaults_to_exactly_one_hour():
    caldav = FakeCalDAV()
    response = event_service(caldav).create_fixed_event(
        title="Dr Morland",
        start=START_ONLY_START,
        end=None,
    )

    assert response.status is CalendarEventCreateStatus.CREATED
    assert response.event.start_iso == START_ONLY_START
    assert response.event.end_iso == START_ONLY_END
    assert response.event.end_iso - response.event.start_iso == timedelta(hours=1)
    assert caldav.created[0]["end"] == START_ONLY_END


def test_explicit_end_remains_authoritative():
    caldav = FakeCalDAV()
    explicit_end = START_ONLY_START + timedelta(hours=2, minutes=30)

    event_service(caldav).create_fixed_event(
        title="Dr Morland",
        start=START_ONLY_START,
        end=explicit_end,
    )

    assert caldav.created[0]["end"] == explicit_end


def test_explicit_duration_remains_authoritative():
    caldav = FakeCalDAV()

    event_service(caldav).create_fixed_event(
        title="Dr Morland",
        start=START_ONLY_START,
        end=None,
        duration_minutes=90,
    )

    assert caldav.created[0]["end"] == START_ONLY_START + timedelta(minutes=90)


def test_missing_start_and_invalid_explicit_end_are_rejected_without_side_effects():
    caldav = FakeCalDAV()
    service = event_service(caldav)

    with pytest.raises(CalendarEventValidationError, match="start time is required"):
        service.create_fixed_event(
            title="Rehearsal",
            start=None,
            end=None,
        )
    with pytest.raises(CalendarEventValidationError, match="after its start"):
        service.create_fixed_event(
            title="Rehearsal",
            start=END,
            end=START,
            duration_minutes=60,
        )

    assert caldav.find_calls == []
    assert caldav.fetch_calls == []
    assert caldav.created == []


def test_conflict_warns_but_does_not_move_or_block_fixed_event():
    conflict = BriefCalendarEvent(
        uid="school-1",
        calendar="school",
        title="Class",
        start_iso=datetime(2026, 8, 10, 13, 0, tzinfo=ZONE),
        end_iso=datetime(2026, 8, 10, 14, 0, tzinfo=ZONE),
    )
    caldav = FakeCalDAV(conflicts=[conflict])
    executor = ActionExecutor(
        vikunja=ForbiddenVikunja(),
        scheduler=ForbiddenScheduler(),
        daily_brief=ForbiddenBrief(),
        calendar_events=event_service(caldav),
    )

    response = executor.execute(
        ActionPlanner(settings()).plan(acceptance_intent(), NOW.date()),
        NOW,
        ZONE,
    )

    assert response.calendar_event.event.start_iso == START
    assert response.calendar_event.event.end_iso == END
    assert response.calendar_event.conflicts[0].title == "Class"
    assert 'Warning: This overlaps with "Class" from 1:00 PM to 2:00 PM' in response.result
    assert len(caldav.created) == 1


@pytest.mark.parametrize(
    ("message", "intent_type", "duration"),
    [
        ("Prepare for the AD Players focus call", IntentType.CREATE_TASK, None),
        ("Buy Liquid IV tomorrow", IntentType.CREATE_TASK, None),
        ("Finish paperwork by Friday", IntentType.CREATE_TASK, None),
        (
            "Schedule 90 minutes tomorrow to prepare paperwork",
            IntentType.SCHEDULE_TASK,
            90,
        ),
        (
            "Schedule paperwork tomorrow from 10:00 to 11:00",
            IntentType.SCHEDULE_TASK,
            60,
        ),
    ],
)
def test_task_and_work_block_language_does_not_become_fixed_event(
    message,
    intent_type,
    duration,
):
    intent = RuleBasedIntentInterpreter(settings()).interpret(
        message,
        date(2026, 8, 3),
    )

    assert intent.intent is intent_type
    assert intent.duration_minutes == duration
    if message.startswith("Prepare"):
        assert intent.title == "Prepare for the AD Players focus call"
        assert intent.location_query is None


def test_gemini_schema_accepts_provider_neutral_calendar_event_fields():
    payload = {
        "intent": "CREATE_CALENDAR_EVENT",
        "title": "Dentist appointment",
        "start_iso": "2026-08-14T14:00:00-05:00",
        "end_iso": "2026-08-14T15:00:00-05:00",
        "calendar_category": "PERSONAL",
        "location_query": "Dental clinic",
    }
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://example.invalid"),
        json={
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(payload)}]}}
            ]
        },
    )

    class Client:
        def __init__(self):
            self.request = None

        def post(self, url, **kwargs):
            self.request = kwargs
            return response

    client = Client()
    intent = GeminiInterpreter(
        api_key="secret",
        model="test-model",
        client=client,
        timezone="America/Chicago",
    ).interpret("Dentist appointment August 14 at 2pm until 3pm", NOW.date())

    assert intent.intent is IntentType.CREATE_CALENDAR_EVENT
    assert intent.calendar_category is CalendarCategory.PERSONAL
    assert intent.location_query == "Dental clinic"
    schema = client.request["json"]["generationConfig"]["responseJsonSchema"]
    assert "start_iso" in schema["properties"]
    assert "end_iso" in schema["properties"]
    assert "location_query" in schema["properties"]
    prompt = client.request["json"]["contents"][0]["parts"][0]["text"]
    assert "Reference date: 2026-08-03" in prompt
    assert "America/Chicago" in prompt


def test_caldav_duplicate_search_excludes_vikunja_work_blocks(monkeypatch):
    def component(description):
        item = Event()
        item.add("uid", description or "normal")
        item.add("summary", "Focus call for Holly Street")
        item.add("description", description)
        item.add("dtstart", START)
        item.add("dtend", END)
        return item

    class Resource:
        def __init__(self, item):
            self.icalendar_component = item
            self.url = "https://example.invalid/event.ics"

    class Calendar:
        def get_display_name(self):
            return "theater"

        def search(self, **kwargs):
            return [
                Resource(component("Vikunja task ID: 42")),
                Resource(component("ordinary fixed event")),
            ]

    service = CalDAVService()
    service.settings = SimpleNamespace(beacon_timezone="America/Chicago")
    monkeypatch.setattr(
        service,
        "_get_client",
        lambda: SimpleNamespace(
            principal=lambda: SimpleNamespace(calendars=lambda: [Calendar()])
        ),
    )

    matches = service.find_fixed_events(
        calendar_name="theater",
        title="FOCUS call for holly street",
        start=START,
        end=END,
    )

    assert len(matches) == 1
    assert matches[0].uid == "ordinary fixed event"


def test_caldav_adapter_writes_canonical_location_property(monkeypatch):
    class Resource:
        url = "https://example.invalid/new.ics"

        def __init__(self):
            self.icalendar_component = Event()
            self.icalendar_component.add("uid", "fixed-location")

    class Calendar:
        def __init__(self):
            self.kwargs = None

        def add_event(self, **kwargs):
            self.kwargs = kwargs
            return Resource()

    calendar = Calendar()
    service = CalDAVService(settings())
    monkeypatch.setattr(
        service,
        "_find_calendar",
        lambda name: (calendar, "theater"),
    )
    location = (
        "A.D. Players at the George Theater, "
        "5420 Westheimer Road, Houston, TX 77056"
    )

    result = service.create_event(
        calendar_name="theater",
        title="Focus call for Holly Street",
        description="",
        start=START,
        end=END,
        location=location,
    )

    assert calendar.kwargs["location"] == location
    assert result.location == location


def interaction_service(caldav):
    return InteractionService(
        vikunja=ForbiddenVikunja(),
        scheduler=ForbiddenScheduler(),
        daily_brief=ForbiddenBrief(),
        calendar_events=event_service(caldav),
        settings=settings(),
        clock=lambda timezone: NOW.astimezone(timezone),
    )


def test_interact_pipeline_uses_fixed_clock_and_calendar_service():
    caldav = FakeCalDAV()
    response = interaction_service(caldav).interact(InteractRequest(message=MESSAGE))

    assert response.intent.intent is IntentType.CREATE_CALENDAR_EVENT
    assert response.intent.start_iso == START
    assert response.calendar_event.event.calendar == "theater"
    assert response.actions_taken[0].action == "calendar_event_created"
    assert len(caldav.created) == 1


def test_reported_start_only_input_reaches_creation_with_one_hour_end():
    caldav = FakeCalDAV()
    response = interaction_service(caldav).interact(
        InteractRequest(message=START_ONLY_MESSAGE)
    )

    assert response.intent.intent is IntentType.CREATE_CALENDAR_EVENT
    assert response.intent.title == "Dr Morland"
    assert response.intent.start_iso == START_ONLY_START
    assert response.intent.end_iso is None
    assert response.intent.duration_minutes is None
    assert response.calendar_event.status is CalendarEventCreateStatus.CREATED
    assert response.calendar_event.event.start_iso == START_ONLY_START
    assert response.calendar_event.event.end_iso == START_ONLY_END
    assert response.result == (
        'Created calendar event "Dr Morland" on Tuesday, August 4, 2026 '
        "from 2:00 PM to 3:00 PM in Personal."
    )
    assert len(caldav.created) == 1


def test_interpreted_event_duration_is_forwarded_through_plan_and_execution():
    caldav = FakeCalDAV()
    response = interaction_service(caldav).interact(
        InteractRequest(message="Dr Morland Aug 4th at 14:00 for 30 minutes")
    )

    assert response.intent.end_iso is None
    assert response.intent.duration_minutes == 30
    assert response.plan.actions[0].duration_minutes == 30
    assert response.calendar_event.event.end_iso == datetime(
        2026, 8, 4, 14, 30, tzinfo=ZONE
    )


def test_interact_endpoint_creates_fixed_event_with_fakes(monkeypatch):
    caldav = FakeCalDAV()
    service = interaction_service(caldav)
    monkeypatch.setattr(interface_route, "InteractionService", lambda: service)

    response = TestClient(app).post(
        "/interact",
        headers={"X-Beacon-API-Key": "test"},
        json={"message": MESSAGE},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intent"]["intent"] == "CREATE_CALENDAR_EVENT"
    assert body["intent"]["title"] == "Focus call for Holly Street"
    assert body["intent"]["location_query"] == "AD Players"
    assert body["calendar_event"]["status"] == "CREATED"
    assert body["calendar_event"]["event"]["calendar"] == "theater"
    assert body["calendar_event"]["event"]["location"].startswith(
        "A.D. Players at the George Theater"
    )
    assert len(caldav.created) == 1
