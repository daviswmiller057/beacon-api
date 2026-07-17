from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import caldav

from app.config import get_settings
from app.models import BusyInterval


class CalDAVService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _to_datetime(self, value: datetime | date, timezone: ZoneInfo) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone)
            return value
        return datetime.combine(value, time.min, tzinfo=timezone)

    def fetch_busy_intervals(
        self,
        start: datetime,
        end: datetime,
        calendar_names: list[str] | None = None,
    ) -> list[BusyInterval]:
        timezone = ZoneInfo(self.settings.beacon_timezone)
        wanted = set(calendar_names or self.settings.calendar_names)
        intervals: list[BusyInterval] = []

        client = caldav.DAVClient(
            url=self.settings.nextcloud_caldav_url,
            username=self.settings.nextcloud_username,
            password=self.settings.nextcloud_app_password,
        )

        principal = client.principal()
        calendars = principal.calendars()

        for calendar in calendars:
            name = (calendar.name or "").strip()
            if name not in wanted:
                continue

            events = calendar.search(
                start=start,
                end=end,
                event=True,
                expand=True,
            )

            for event in events:
                vevent = event.vobject_instance.vevent
                event_start = self._to_datetime(vevent.dtstart.value, timezone)
                event_end = self._to_datetime(vevent.dtend.value, timezone)
                title = getattr(getattr(vevent, "summary", None), "value", None)

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
