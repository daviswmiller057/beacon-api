from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import caldav

from app.config import get_settings
from app.models import BusyInterval, CalendarEventResult


class CalDAVService:
    def __init__(self) -> None:
        self.settings = get_settings()

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

    def fetch_busy_intervals(
        self,
        start: datetime,
        end: datetime,
        calendar_names: list[str] | None = None,
    ) -> list[BusyInterval]:
        timezone = ZoneInfo(self.settings.beacon_timezone)

        wanted = {
            name.strip().casefold()
            for name in (
                calendar_names or self.settings.calendar_names
            )
        }

        intervals: list[BusyInterval] = []

        client = self._get_client()
        principal = client.principal()
        calendars = principal.calendars()

        for calendar in calendars:
            name = (calendar.get_display_name() or "").strip()

            if name.casefold() not in wanted:
                continue

            events = calendar.search(
                start=start,
                end=end,
                event=True,
                expand=True,
            )

            for event in events:
                component = event.icalendar_component

                if component is None or component.name != "VEVENT":
                    continue

                event_start = self._to_datetime(
                    component.decoded("DTSTART"),
                    timezone,
                )

                if "DTEND" in component:
                    event_end = self._to_datetime(
                        component.decoded("DTEND"),
                        timezone,
                    )
                elif "DURATION" in component:
                    event_end = (
                        event_start
                        + component.decoded("DURATION")
                    )
                else:
                    event_end = event_start

                title = str(component.get("SUMMARY", "")) or None

                if event_end <= start or event_start >= end:
                    continue

                intervals.append(
                    BusyInterval(
                        start_iso=max(event_start, start),
                        end_iso=min(event_end, end),
                        calendar=name,
                        title=title,
                    )
                )

        return intervals

    def find_task_event(
        self,
        calendar_name: str,
        task_id: int,
        search_start: datetime,
        search_end: datetime,
    ) -> CalendarEventResult | None:
        timezone = ZoneInfo(self.settings.beacon_timezone)
        calendar, resolved_name = self._find_calendar(calendar_name)

        marker = f"Vikunja task ID: {task_id}"

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

            if marker not in description:
                continue

            event_start = self._to_datetime(
                component.decoded("DTSTART"),
                timezone,
            )

            if "DTEND" in component:
                event_end = self._to_datetime(
                    component.decoded("DTEND"),
                    timezone,
                )
            elif "DURATION" in component:
                event_end = (
                    event_start
                    + component.decoded("DURATION")
                )
            else:
                event_end = event_start

            title = (
                str(component.get("SUMMARY", ""))
                or f"Work Block — Task {task_id}"
            )

            uid = None

            if component.get("UID"):
                uid = str(component.get("UID"))

            href = None

            if getattr(event, "url", None):
                href = str(event.url)

            return CalendarEventResult(
                uid=uid,
                href=href,
                calendar=resolved_name,
                title=title,
                start_iso=event_start,
                end_iso=event_end,
            )

        return None

    def create_event(
        self,
        calendar_name: str,
        title: str,
        description: str,
        start: datetime,
        end: datetime,
    ) -> CalendarEventResult:
        if end <= start:
            raise ValueError(
                "Calendar event end must be after its start"
            )

        calendar, resolved_name = self._find_calendar(
            calendar_name
        )

        event = calendar.add_event(
            dtstart=start,
            dtend=end,
            summary=title,
            description=description,
        )

        component = event.icalendar_component

        uid = None

        if component is not None and component.get("UID"):
            uid = str(component.get("UID"))

        href = None

        if getattr(event, "url", None):
            href = str(event.url)

        return CalendarEventResult(
            uid=uid,
            href=href,
            calendar=resolved_name,
            title=title,
            start_iso=start,
            end_iso=end,
        )