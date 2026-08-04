import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.intake.errors import UnsupportedIntentError
from app.models import CalendarCategory, IntentType, StructuredIntent


class RuleBasedIntentInterpreter:
    """Small, deterministic fallback for Beacon's minimum useful commands."""

    _duration = re.compile(
        r"\bfor\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\b",
        re.IGNORECASE,
    )
    _task_id = re.compile(r"(?:\btask\s+|#)(\d+)\b", re.IGNORECASE)
    _leading_schedule_duration = re.compile(
        r"^(?P<prefix>please\s+)?schedule\s+"
        r"(?P<amount>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>minutes?|mins?|hours?|hrs?)\b",
        re.IGNORECASE,
    )
    _weekdays = (
        "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    )
    _months = (
        "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        "jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        "nov(?:ember)?|dec(?:ember)?"
    )
    _time_token = (
        r"(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)?"
    )
    _event_range = re.compile(
        rf"\b(?:from|at)\s+(?P<start>{_time_token})\s*"
        rf"(?:-|–|—|to|until)\s*(?P<end>{_time_token})",
        re.IGNORECASE,
    )
    _event_start = re.compile(
        rf"\b(?:from|at)\s+(?P<start>{_time_token})",
        re.IGNORECASE,
    )
    _numeric_date = re.compile(
        rf"\b(?:(?P<weekday>{_weekdays})\s+)?"
        r"(?P<month>\d{1,2})/(?P<day>\d{1,2})"
        r"(?:/(?P<year>\d{2}|\d{4}))?\b",
        re.IGNORECASE,
    )
    _named_date = re.compile(
        rf"\b(?P<month>{_months})\s+(?P<day>\d{{1,2}})"
        r"(?:st|nd|rd|th)?"
        r"(?:,?\s+(?P<year>\d{4}))?\b",
        re.IGNORECASE,
    )
    _relative_date = re.compile(r"\b(today|tomorrow)\b", re.IGNORECASE)
    _weekday_date = re.compile(rf"\b({_weekdays})\b", re.IGNORECASE)
    _explicit_location = re.compile(
        r"^(?P<title>.+?)\s+(?:at|in(?:\s+the)?|on)\s+(?P<location>.+)$",
        re.IGNORECASE,
    )
    _instruction = re.compile(
        r"^(?:use|bring|park|call|meet)\b",
        re.IGNORECASE,
    )
    _virtual_locations = {
        "zoom": "Zoom",
        "google meet": "Google Meet",
        "microsoft teams": "Microsoft Teams",
        "discord": "Discord",
        "phone call": "Phone call",
        "online": "Online",
    }
    _implicit_venues = {
        "a.d. players": "AD Players",
        "ad players": "AD Players",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def interpret(
        self,
        message: str,
        reference_date: date | None = None,
    ) -> StructuredIntent:
        today = reference_date or datetime.now(
            ZoneInfo(self.settings.beacon_timezone)
        ).date()
        compact = " ".join(message.strip().split())
        lowered = compact.casefold()
        target_date = self._target_date(lowered, today)

        explicit_task = bool(
            re.match(
                r"^(please\s+)?(?:add|create)(?:\s+a)?\s+task\b",
                lowered,
            )
        )
        is_schedule = bool(re.match(r"^(please\s+)?schedule\b", lowered))
        fixed_event = (
            None
            if explicit_task or is_schedule
            else self._fixed_event(compact, today)
        )
        if fixed_event is not None:
            return fixed_event

        if not is_schedule and self._is_brief_request(lowered):
            return StructuredIntent(intent=IntentType.BRIEF, deadline=target_date)

        is_create = bool(
            re.match(
                r"^(please\s+)?(?:add|create|remember|buy|prepare|finish)\b",
                lowered,
            )
        )
        if not is_schedule and is_create:
            title = re.sub(r"^please\s+", "", compact, flags=re.IGNORECASE)
            if not re.match(
                r"^(?:buy|prepare|finish)\b", title, flags=re.IGNORECASE
            ):
                title = re.sub(
                    r"^(?:(?:add|create)(?:\s+a)?(?:\s+task)?(?:\s+to)?\s+|remember\s+to\s+)",
                    "",
                    title,
                    flags=re.IGNORECASE,
                )
            title = self._strip_task_date(title)
            title = " ".join(title.strip(" .,!?").split())
            if not title:
                raise UnsupportedIntentError(
                    "Name the task you want Beacon to create."
                )
            return StructuredIntent(
                intent=IntentType.CREATE_TASK,
                title=title,
                deadline=target_date,
            )

        if not is_schedule:
            raise UnsupportedIntentError(
                "I can currently create a task, create a fixed calendar event, "
                "produce a brief, or schedule work."
            )

        leading_duration = self._leading_schedule_duration.match(compact)
        if leading_duration:
            duration = self._duration_minutes(
                leading_duration.group("amount"),
                leading_duration.group("unit"),
            )
            prefix = (
                "Please schedule "
                if leading_duration.group("prefix")
                else "Schedule "
            )
            compact = prefix + compact[leading_duration.end() :].lstrip()
        else:
            duration, compact = self._extract_duration(compact)
        id_match = self._task_id.search(compact)
        if id_match:
            return StructuredIntent(
                intent=IntentType.SCHEDULE_TASK,
                task_id=int(id_match.group(1)),
                deadline=target_date,
                duration_minutes=duration,
            )

        title = re.sub(
            r"^(please\s+)?schedule\s+", "", compact, flags=re.IGNORECASE
        )
        title = re.sub(
            r"\b(?:(?:for|on)\s+)?(?:today|tomorrow)\b",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(r"^\s*to\s+", "", title, flags=re.IGNORECASE)
        title = re.sub(
            r"\s+(?:on|in)\s+(?:my\s+)?calendar\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = " ".join(title.strip(" .,!?").split())
        if not title:
            raise UnsupportedIntentError(
                "Name the Vikunja task to schedule, or use its task ID."
            )
        return StructuredIntent(
            intent=IntentType.SCHEDULE_TASK,
            title=title,
            deadline=target_date,
            duration_minutes=duration,
        )

    def _fixed_event(
        self,
        message: str,
        today: date,
    ) -> StructuredIntent | None:
        parsed_date = self._parse_date(message, today)
        if parsed_date is None:
            return None
        event_date, date_span = parsed_date
        range_match = self._event_range.search(message)
        start_match = range_match or self._event_start.search(message)
        if start_match is None:
            return None

        start_text = start_match.group("start")
        end_text = range_match.group("end") if range_match else None
        start_meridiem = self._meridiem(start_text)
        end_meridiem = self._meridiem(end_text) if end_text else None
        start_time = self._parse_time(
            start_text,
            fallback_meridiem=end_meridiem,
        )
        end_time = (
            self._parse_time(end_text, fallback_meridiem=start_meridiem)
            if end_text
            else None
        )
        timezone = ZoneInfo(self.settings.beacon_timezone)
        start_at = datetime.combine(event_date, start_time, tzinfo=timezone)
        end_at = (
            datetime.combine(event_date, end_time, tzinfo=timezone)
            if end_time
            else None
        )

        title_end = min(date_span[0], start_match.start())
        event_head = message[:title_end]
        event_head = re.sub(
            r"\s+(?:on|for)\s*$",
            "",
            event_head,
            flags=re.IGNORECASE,
        )
        title, location_query = self._event_details(event_head)
        if not title:
            raise UnsupportedIntentError("Name the calendar event to create.")
        description = self._event_description(
            message[(range_match or start_match).end() :]
        )
        duration_match = self._duration.search(message)
        duration_minutes = (
            self._duration_minutes(
                duration_match.group(1),
                duration_match.group(2),
            )
            if duration_match
            else None
        )
        return StructuredIntent(
            intent=IntentType.CREATE_CALENDAR_EVENT,
            title=title,
            start_iso=start_at,
            end_iso=end_at,
            duration_minutes=duration_minutes,
            calendar_category=self._event_category(title, location_query),
            location_query=location_query,
            description=description,
        )

    @classmethod
    def _event_details(cls, value: str) -> tuple[str, str | None]:
        cleaned = " ".join(value.strip(" .,!?").split())
        explicit = cls._explicit_location.match(cleaned)
        if explicit:
            title = explicit.group("title")
            location = re.sub(
                r"^the\s+",
                "",
                explicit.group("location"),
                flags=re.IGNORECASE,
            )
            return cls._capitalize(title), " ".join(location.split())

        normalized = cleaned.casefold()
        for virtual, canonical in cls._virtual_locations.items():
            prefix = f"{virtual} "
            if normalized.startswith(prefix):
                title = cleaned[len(prefix) :]
                if virtual == "phone call" and title.casefold().startswith("with "):
                    title = f"Call {title}"
                return cls._capitalize(title), canonical
        for venue, query in cls._implicit_venues.items():
            prefix = f"{venue} "
            if normalized.startswith(prefix):
                return cls._capitalize(cleaned[len(prefix) :]), query
        return cls._capitalize(cleaned), None

    @classmethod
    def _event_description(cls, value: str) -> str | None:
        cleaned = " ".join(value.lstrip(" ,;:-").strip(" .").split())
        if not cleaned or not cls._instruction.match(cleaned):
            return None
        return cls._capitalize(cleaned)

    @staticmethod
    def _event_category(
        title: str,
        location_query: str | None,
    ) -> CalendarCategory | None:
        text = f"{title} {location_query or ''}".casefold()
        if any(
            term in text
            for term in (
                "ad players",
                "focus call",
                "load-in",
                "load in",
                "performance",
                "rehearsal",
                "strike",
                "tech",
                "theater",
                "theatre",
            )
        ):
            return CalendarCategory.THEATER
        if any(
            term in text
            for term in ("class", "exam", "lecture", "school", "uh")
        ):
            return CalendarCategory.SCHOOL
        return None

    @staticmethod
    def _capitalize(value: str) -> str:
        cleaned = " ".join(value.strip(" .,!?").split())
        return cleaned[:1].upper() + cleaned[1:] if cleaned else ""

    def _parse_date(
        self,
        message: str,
        today: date,
    ) -> tuple[date, tuple[int, int]] | None:
        numeric = self._numeric_date.search(message)
        if numeric:
            year_text = numeric.group("year")
            year = self._resolve_year(year_text, today.year)
            month = int(numeric.group("month"))
            day = int(numeric.group("day"))
            result = self._safe_date(year, month, day)
            if year_text is None and result < today:
                result = self._safe_date(year + 1, month, day)
            weekday = numeric.group("weekday")
            if weekday and result.strftime("%A").casefold() != weekday.casefold():
                raise UnsupportedIntentError(
                    f"{weekday.title()} does not match {month}/{day}/{result.year}."
                )
            return result, numeric.span()

        named = self._named_date.search(message)
        if named:
            month = self._month_number(named.group("month"))
            year = int(named.group("year") or today.year)
            result = self._safe_date(year, month, int(named.group("day")))
            if named.group("year") is None and result < today:
                result = self._safe_date(year + 1, month, result.day)
            return result, named.span()

        relative = self._relative_date.search(message)
        if relative:
            offset = 1 if relative.group(1).casefold() == "tomorrow" else 0
            return today + timedelta(days=offset), relative.span()

        weekday = self._weekday_date.search(message)
        if weekday:
            names = [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ]
            target = names.index(weekday.group(1).casefold())
            days_out = (target - today.weekday()) % 7 or 7
            return today + timedelta(days=days_out), weekday.span()
        return None

    @staticmethod
    def _month_number(value: str) -> int:
        months = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        return months[value[:3].casefold()]

    def _extract_duration(self, message: str) -> tuple[int, str]:
        match = self._duration.search(message)
        if not match:
            return self.settings.beacon_interaction_default_duration_minutes, message
        minutes = self._duration_minutes(match.group(1), match.group(2))
        return minutes, (message[: match.start()] + message[match.end() :])

    @staticmethod
    def _duration_minutes(amount_text: str, unit_text: str) -> int:
        amount = float(amount_text)
        unit = unit_text.casefold()
        minutes = round(amount * 60) if unit.startswith("h") else round(amount)
        if not 1 <= minutes <= 1440:
            raise UnsupportedIntentError(
                "Work block duration must be between 1 minute and 24 hours."
            )
        return minutes

    def _target_date(self, message: str, today: date) -> date | None:
        parsed = self._parse_date(message, today)
        return parsed[0] if parsed else None

    @classmethod
    def _strip_task_date(cls, title: str) -> str:
        title = re.sub(
            r"\b(?:(?:by|on)\s+)?(?:today|tomorrow)\b",
            "",
            title,
            flags=re.IGNORECASE,
        )
        return re.sub(
            rf"\b(?:by|on)\s+(?:{cls._weekdays})\b",
            "",
            title,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _resolve_year(value: str | None, current_year: int) -> int:
        if value is None:
            return current_year
        year = int(value)
        return 2000 + year if len(value) == 2 else year

    @staticmethod
    def _safe_date(year: int, month: int, day: int) -> date:
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise UnsupportedIntentError(
                f"Invalid calendar event date: {month}/{day}/{year}."
            ) from exc

    @staticmethod
    def _meridiem(value: str | None) -> str | None:
        if not value:
            return None
        match = re.search(r"([ap])\.?m\.?", value, re.IGNORECASE)
        return match.group(1).casefold() if match else None

    @classmethod
    def _parse_time(
        cls,
        value: str,
        fallback_meridiem: str | None = None,
    ) -> time:
        cleaned = value.strip().casefold().replace(".", "")
        meridiem = cls._meridiem(cleaned) or fallback_meridiem
        digits = re.sub(r"\s*[ap]m\s*$", "", cleaned)
        hour_text, separator, minute_text = digits.partition(":")
        hour = int(hour_text)
        minute = int(minute_text) if separator else 0
        if meridiem:
            if not 1 <= hour <= 12:
                raise UnsupportedIntentError(f'Invalid event time "{value}".')
            if meridiem == "p" and hour != 12:
                hour += 12
            if meridiem == "a" and hour == 12:
                hour = 0
        try:
            return time(hour, minute)
        except ValueError as exc:
            raise UnsupportedIntentError(f'Invalid event time "{value}".') from exc

    @staticmethod
    def _is_brief_request(message: str) -> bool:
        return any(
            phrase in message
            for phrase in (
                "brief",
                "what's on",
                "what is on",
                "what do i have",
                "my day",
                "status",
            )
        )
