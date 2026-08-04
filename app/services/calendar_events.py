import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.models import (
    CalendarCategory,
    CalendarEventConflict,
    CalendarEventCreateStatus,
    CreateCalendarEventResponse,
    LocationCandidate,
    LocationResolution,
    LocationResolutionStatus,
)
from app.services.caldav_client import CalDAVService
from app.services.location import LocationResolver, build_location_resolver


class CalendarEventValidationError(ValueError):
    pass


class CalendarEventRoutingError(ValueError):
    pass


class CalendarEventService:
    """Deterministic fixed-event validation, routing, and CalDAV lifecycle."""

    _THEATER_TERMS = {
        "ad players",
        "focus",
        "load in",
        "performance",
        "rehearsal",
        "strike",
        "tech",
        "theater",
        "theatre",
    }
    _SCHOOL_TERMS = {
        "class",
        "exam",
        "lecture",
        "school",
        "uh",
    }
    _VIRTUAL_LOCATIONS = {
        "zoom": "Zoom",
        "google meet": "Google Meet",
        "microsoft teams": "Microsoft Teams",
        "teams": "Microsoft Teams",
        "discord": "Discord",
        "phone call": "Phone call",
        "online": "Online",
    }

    def __init__(
        self,
        *,
        caldav: CalDAVService | None = None,
        location_resolver: LocationResolver | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.caldav = caldav or CalDAVService(self.settings)
        self.location_resolver = (
            location_resolver
            if location_resolver is not None
            else build_location_resolver(self.settings)
        )

    def create_fixed_event(
        self,
        *,
        title: str | None,
        start: datetime | None,
        end: datetime | None,
        duration_minutes: int | None = None,
        calendar_category: CalendarCategory | None = None,
        location_query: str | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> CreateCalendarEventResponse:
        normalized_title = " ".join((title or "").split())
        if not normalized_title:
            raise CalendarEventValidationError(
                "Calendar event title is required."
            )
        timezone = ZoneInfo(self.settings.beacon_timezone)
        local_start, local_end = self._normalize_bounds(
            start=start,
            end=end,
            duration_minutes=duration_minutes,
            timezone=timezone,
        )
        if local_end <= local_start:
            raise CalendarEventValidationError(
                "Calendar event end time must be after its start time."
            )

        calendar_name = self.route_calendar(
            title=normalized_title,
            calendar_category=calendar_category,
            location=location_query or location,
            description=description,
        )
        duplicates = self.caldav.find_fixed_events(
            calendar_name=calendar_name,
            title=normalized_title,
            start=local_start,
            end=local_end,
        )
        if duplicates:
            return CreateCalendarEventResponse(
                status=CalendarEventCreateStatus.EXISTING,
                event=duplicates[0],
            )

        resolved_location, resolution, warnings = self._resolve_location(
            location_query=location_query,
            location=location,
        )
        if (
            resolution is not None
            and resolution.status is LocationResolutionStatus.AMBIGUOUS
        ):
            return CreateCalendarEventResponse(
                status=CalendarEventCreateStatus.CLARIFICATION,
                location_resolution=resolution,
                clarification_question=self._clarification(resolution),
            )

        existing_events = self.caldav.fetch_calendar_events(
            local_start,
            local_end,
            calendar_names=self.settings.calendar_names,
        )
        conflicts = [
            CalendarEventConflict(
                calendar=event.calendar,
                title=event.title,
                start_iso=event.start_iso,
                end_iso=event.end_iso,
            )
            for event in existing_events
            if event.start_iso < local_end and event.end_iso > local_start
        ]
        created = self.caldav.create_event(
            calendar_name=calendar_name,
            title=normalized_title,
            description=(description or "").strip(),
            location=resolved_location,
            start=local_start,
            end=local_end,
        )
        return CreateCalendarEventResponse(
            status=CalendarEventCreateStatus.CREATED,
            event=created,
            conflicts=conflicts,
            location_resolution=resolution,
            warnings=warnings,
            notices=(
                [f"Location data {resolution.attribution}."]
                if resolution is not None
                and resolution.status is LocationResolutionStatus.RESOLVED
                and resolution.attribution
                else []
            ),
        )

    @classmethod
    def _normalize_bounds(
        cls,
        *,
        start: datetime | None,
        end: datetime | None,
        duration_minutes: int | None,
        timezone: ZoneInfo,
    ) -> tuple[datetime, datetime]:
        if start is None:
            raise CalendarEventValidationError(
                "Calendar event start time is required."
            )
        local_start = cls._localize(start, timezone)
        if end is not None:
            return local_start, cls._localize(end, timezone)
        if duration_minutes is not None:
            if not 1 <= duration_minutes <= 24 * 60:
                raise CalendarEventValidationError(
                    "Calendar event duration must be between 1 minute and 24 hours."
                )
            return local_start, local_start + timedelta(minutes=duration_minutes)
        return local_start, local_start + timedelta(hours=1)

    def _resolve_location(
        self,
        *,
        location_query: str | None,
        location: str | None,
    ) -> tuple[str | None, LocationResolution | None, list[str]]:
        supplied_location = " ".join((location or "").split())
        if supplied_location:
            return supplied_location, None, []

        query = " ".join((location_query or "").split())
        if not query:
            return None, None, []
        virtual = self._VIRTUAL_LOCATIONS.get(self._normalize(query))
        if virtual:
            return (
                virtual,
                LocationResolution(
                    query=query,
                    status=LocationResolutionStatus.SKIPPED,
                    selected=LocationCandidate(canonical_name=virtual, confidence=1),
                    detail="virtual location",
                ),
                [],
            )
        if self.location_resolver is None:
            return (
                query,
                LocationResolution(
                    query=query,
                    status=LocationResolutionStatus.SKIPPED,
                    detail="location lookup is disabled",
                ),
                [
                    f'Address lookup is disabled; using unverified location "{query}".'
                ],
            )

        resolution = self.location_resolver.resolve(
            query,
            geographic_bias=(
                self.settings.beacon_location_bias
                or self.settings.beacon_home_location
            ),
        )
        if (
            resolution.status is LocationResolutionStatus.RESOLVED
            and resolution.selected is not None
        ):
            return self._candidate_location(resolution.selected), resolution, []
        if resolution.status is LocationResolutionStatus.AMBIGUOUS:
            return None, resolution, []
        if resolution.status is LocationResolutionStatus.UNAVAILABLE:
            return (
                query,
                resolution,
                [
                    f'Address resolution was unavailable; using location "{query}".'
                ],
            )
        return (
            query,
            resolution,
            [
                f'Location "{query}" could not be verified; using the venue name '
                "without an address."
            ],
        )

    @classmethod
    def _candidate_location(cls, candidate: LocationCandidate) -> str:
        name = " ".join(candidate.canonical_name.split())
        address = " ".join((candidate.formatted_address or "").split())
        if not address:
            return name
        normalized_name = cls._normalize(name)
        normalized_address = cls._normalize(address)
        if normalized_address == normalized_name or normalized_address.startswith(
            f"{normalized_name} "
        ):
            return address
        return f"{name}, {address}"

    @staticmethod
    def _clarification(resolution: LocationResolution) -> str:
        lines = [f'I found multiple matches for "{resolution.query}":']
        for index, candidate in enumerate(resolution.alternatives[:3], start=1):
            display = candidate.canonical_name
            if candidate.formatted_address:
                display += f" — {candidate.formatted_address}"
            lines.append(f"{index}. {display}")
        lines.append("Please provide a more specific venue.")
        return "\n".join(lines)

    def route_calendar(
        self,
        *,
        title: str,
        calendar_category: CalendarCategory | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> str:
        routing_text = self._normalize(
            " ".join(
                value for value in (title, location, description) if value
            )
        )
        if self._contains_any(routing_text, self._THEATER_TERMS):
            category = CalendarCategory.THEATER
        elif self._contains_any(routing_text, self._SCHOOL_TERMS):
            category = CalendarCategory.SCHOOL
        else:
            category = calendar_category or CalendarCategory.PERSONAL

        requested = category.value.casefold()
        configured = {
            name.strip().casefold(): name.strip()
            for name in self.settings.calendar_names
        }
        if requested not in configured:
            raise CalendarEventRoutingError(
                f'Calendar "{category.value.casefold()}" is not configured '
                "in BEACON_CALENDARS."
            )
        return configured[requested]

    @staticmethod
    def _localize(value: datetime, timezone: ZoneInfo) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone)
        return value.astimezone(timezone)

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())

    @classmethod
    def _contains_any(cls, value: str, terms: set[str]) -> bool:
        padded = f" {value} "
        return any(f" {cls._normalize(term)} " in padded for term in terms)
