import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import caldav
from caldav.lib import error as caldav_error

from app.config import Settings, get_settings
from app.models import BriefCalendarEvent, BusyInterval, CalendarEventResult


class CalDAVError(RuntimeError):
    pass


class CalendarEventNotFoundError(CalDAVError):
    pass


class CalendarEventUpdateError(CalDAVError):
    pass


@dataclass
class CalendarEventMatch:
    result: CalendarEventResult
    description: str
    resource: Any


class CalDAVService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _get_client(self) -> caldav.DAVClient:
        return caldav.DAVClient(
            url=self.settings.nextcloud_caldav_url,
            username=self.settings.nextcloud_username,
            password=self.settings.nextcloud_app_password,
        )

    def _to_datetime(
        self,
        value: datetime | date,
        timezone: ZoneInfo,
    ) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone)
            return value.astimezone(timezone)
        return datetime.combine(value, time.min, tzinfo=timezone)

    def _find_calendar(self, calendar_name: str):
        client = self._get_client()
        principal = client.principal()
        for calendar in principal.calendars():
            name = (calendar.get_display_name() or "").strip()
            if name.casefold() == calendar_name.strip().casefold():
                return calendar, name
        raise ValueError(
            f'Calendar "{calendar_name}" was not found in Nextcloud'
        )

    def _event_times(
        self,
        component: Any,
        timezone: ZoneInfo,
    ) -> tuple[datetime, datetime]:
        event_start = self._to_datetime(
            component.decoded("DTSTART"), timezone
        )
        if "DTEND" in component:
            event_end = self._to_datetime(
                component.decoded("DTEND"), timezone
            )
        elif "DURATION" in component:
            event_end = event_start + component.decoded("DURATION")
        else:
            event_end = event_start
        return event_start, event_end

    @staticmethod
    def _has_task_marker(description: str, task_id: int) -> bool:
        marker = f"Vikunja task ID: {task_id}"
        return marker in description.splitlines()

    @staticmethod
    def _task_id_from_description(description: str) -> int | None:
        prefix = "Vikunja task ID: "
        for line in description.splitlines():
            if not line.startswith(prefix):
                continue
            value = line.removeprefix(prefix)
            if value.isdigit():
                return int(value)
        return None

    def _event_result(
        self,
        event: Any,
        component: Any,
        calendar_name: str,
        fallback_title: str,
    ) -> CalendarEventResult:
        timezone = ZoneInfo(self.settings.beacon_timezone)
        event_start, event_end = self._event_times(component, timezone)
        title = str(component.get("SUMMARY", "")) or fallback_title
        uid = str(component.get("UID")) if component.get("UID") else None
        href = str(event.url) if getattr(event, "url", None) else None
        return CalendarEventResult(
            uid=uid,
            href=href,
            calendar=calendar_name,
            title=title,
            start_iso=event_start,
            end_iso=event_end,
            location=str(component.get("LOCATION", "")).strip() or None,
        )

    def fetch_busy_intervals(
        self,
        start: datetime,
        end: datetime,
        calendar_names: list[str] | None = None,
        exclude_task_id: int | None = None,
    ) -> list[BusyInterval]:
        timezone = ZoneInfo(self.settings.beacon_timezone)
        wanted = {
            name.strip().casefold()
            for name in (calendar_names or self.settings.calendar_names)
        }
        intervals: list[BusyInterval] = []
        client = self._get_client()
        principal = client.principal()

        for calendar in principal.calendars():
            name = (calendar.get_display_name() or "").strip()
            if name.casefold() not in wanted:
                continue
            events = calendar.search(
                start=start, end=end, event=True, expand=True
            )
            for event in events:
                component = event.icalendar_component
                if component is None or component.name != "VEVENT":
                    continue
                description = str(component.get("DESCRIPTION", ""))
                if (
                    exclude_task_id is not None
                    and self._has_task_marker(description, exclude_task_id)
                ):
                    continue
                event_start, event_end = self._event_times(
                    component, timezone
                )
                if event_end <= start or event_start >= end:
                    continue
                intervals.append(
                    BusyInterval(
                        start_iso=max(event_start, start),
                        end_iso=min(event_end, end),
                        calendar=name,
                        title=str(component.get("SUMMARY", "")) or None,
                    )
                )
        return intervals

    def fetch_calendar_events(
        self,
        start: datetime,
        end: datetime,
        calendar_names: list[str] | None = None,
    ) -> list[BriefCalendarEvent]:
        timezone = ZoneInfo(self.settings.beacon_timezone)
        wanted = {
            name.strip().casefold()
            for name in (calendar_names or self.settings.calendar_names)
        }
        results: list[BriefCalendarEvent] = []
        client = self._get_client()
        principal = client.principal()

        for calendar in principal.calendars():
            name = (calendar.get_display_name() or "").strip()
            if name.casefold() not in wanted:
                continue
            events = calendar.search(
                start=start, end=end, event=True, expand=True
            )
            for event in events:
                component = event.icalendar_component
                if component is None or component.name != "VEVENT":
                    continue
                raw_start = component.decoded("DTSTART")
                event_start, event_end = self._event_times(
                    component, timezone
                )
                if event_end <= start or event_start >= end:
                    continue
                description = str(component.get("DESCRIPTION", ""))
                task_id = self._task_id_from_description(description)
                uid = (
                    str(component.get("UID"))
                    if component.get("UID")
                    else None
                )
                location = str(component.get("LOCATION", "")).strip() or None
                results.append(
                    BriefCalendarEvent(
                        uid=uid,
                        calendar=name,
                        title=str(component.get("SUMMARY", ""))
                        or "Untitled event",
                        description=description,
                        location=location,
                        start_iso=event_start,
                        end_iso=event_end,
                        all_day=not isinstance(raw_start, datetime),
                        is_beacon_work_block=task_id is not None,
                        vikunja_task_id=task_id,
                    )
                )
        return sorted(
            results,
            key=lambda item: (
                item.start_iso,
                item.end_iso,
                item.calendar.casefold(),
                item.title.casefold(),
            ),
        )

    def find_task_events(
        self,
        calendar_name: str,
        task_id: int,
        search_start: datetime,
        search_end: datetime,
    ) -> list[CalendarEventMatch]:
        calendar, resolved_name = self._find_calendar(calendar_name)
        matches: list[CalendarEventMatch] = []
        events = calendar.search(
            start=search_start,
            end=search_end,
            event=True,
            expand=True,
        )
        for event in events:
            component = event.icalendar_component
            if component is None or component.name != "VEVENT":
                continue
            description = str(component.get("DESCRIPTION", ""))
            if not self._has_task_marker(description, task_id):
                continue
            matches.append(
                CalendarEventMatch(
                    result=self._event_result(
                        event,
                        component,
                        resolved_name,
                        f"Work Block — Task {task_id}",
                    ),
                    description=description,
                    resource=event,
                )
            )
        return matches

    def find_task_event(
        self,
        calendar_name: str,
        task_id: int,
        search_start: datetime,
        search_end: datetime,
    ) -> CalendarEventResult | None:
        matches = self.find_task_events(
            calendar_name, task_id, search_start, search_end
        )
        return matches[0].result if matches else None

    def find_fixed_events(
        self,
        calendar_name: str,
        title: str,
        start: datetime,
        end: datetime,
    ) -> list[CalendarEventResult]:
        calendar, resolved_name = self._find_calendar(calendar_name)
        timezone = ZoneInfo(self.settings.beacon_timezone)
        wanted_title = self._normalize_title(title)
        wanted_start = self._to_datetime(start, timezone)
        wanted_end = self._to_datetime(end, timezone)
        matches: list[CalendarEventResult] = []
        events = calendar.search(
            start=wanted_start,
            end=wanted_end,
            event=True,
            expand=True,
        )
        for event in events:
            component = event.icalendar_component
            if component is None or component.name != "VEVENT":
                continue
            description = str(component.get("DESCRIPTION", ""))
            if self._task_id_from_description(description) is not None:
                continue
            event_start, event_end = self._event_times(component, timezone)
            event_title = str(component.get("SUMMARY", ""))
            if (
                self._normalize_title(event_title) == wanted_title
                and event_start == wanted_start
                and event_end == wanted_end
            ):
                matches.append(
                    self._event_result(
                        event,
                        component,
                        resolved_name,
                        title,
                    )
                )
        return matches

    def create_event(
        self,
        calendar_name: str,
        title: str,
        description: str,
        start: datetime,
        end: datetime,
        location: str | None = None,
    ) -> CalendarEventResult:
        if end <= start:
            raise ValueError("Calendar event end must be after its start")
        calendar, resolved_name = self._find_calendar(calendar_name)
        event_data = {
            "dtstart": start,
            "dtend": end,
            "summary": title,
            "description": description,
        }
        if location:
            event_data["location"] = location
        event = calendar.add_event(
            **event_data,
        )
        component = event.icalendar_component
        uid = (
            str(component.get("UID"))
            if component is not None and component.get("UID")
            else None
        )
        href = str(event.url) if getattr(event, "url", None) else None
        return CalendarEventResult(
            uid=uid,
            href=href,
            calendar=resolved_name,
            title=title,
            start_iso=start,
            end_iso=end,
            location=location,
        )

    @staticmethod
    def _normalize_title(value: str) -> str:
        return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())

    def update_event(
        self,
        match: CalendarEventMatch,
        task_id: int,
        start: datetime,
        end: datetime,
    ) -> CalendarEventResult:
        if end <= start:
            raise ValueError("Calendar event end must be after its start")
        try:
            match.resource.load()
            component = match.resource.icalendar_component
            if component is None or component.name != "VEVENT":
                raise CalendarEventNotFoundError(
                    f"Beacon event for Vikunja task {task_id} is missing"
                )
            description = str(component.get("DESCRIPTION", ""))
            if not self._has_task_marker(description, task_id):
                raise CalendarEventNotFoundError(
                    f"Beacon event for Vikunja task {task_id} is stale"
                )
            current_uid = (
                str(component.get("UID")) if component.get("UID") else None
            )
            if (
                match.result.uid
                and current_uid
                and current_uid != match.result.uid
            ):
                raise CalendarEventNotFoundError(
                    f"Beacon event for Vikunja task {task_id} changed during scheduling"
                )
            if "DTEND" not in component or "DURATION" in component:
                raise CalendarEventUpdateError(
                    f"Beacon event for Vikunja task {task_id} has unsupported duration properties"
                )
            del component["DTSTART"]
            component.add("DTSTART", start)
            del component["DTEND"]
            component.add("DTEND", end)
            match.resource.save(no_create=True, increase_seqno=False)
            return self._event_result(
                match.resource,
                component,
                match.result.calendar,
                match.result.title,
            )
        except CalendarEventNotFoundError:
            raise
        except caldav_error.NotFoundError as exc:
            raise CalendarEventNotFoundError(
                f"Beacon event for Vikunja task {task_id} no longer exists"
            ) from exc
        except Exception as exc:
            raise CalendarEventUpdateError(
                f"Could not update Beacon event for Vikunja task {task_id}: {exc}"
            ) from exc
