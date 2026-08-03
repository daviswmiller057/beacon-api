import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.intake.errors import UnsupportedIntentError
from app.models import IntentType, StructuredIntent


class RuleBasedIntentInterpreter:
    """Small, deterministic fallback for Beacon's minimum useful commands."""

    _duration = re.compile(
        r"\bfor\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\b",
        re.IGNORECASE,
    )
    _task_id = re.compile(r"(?:\btask\s+|#)(\d+)\b", re.IGNORECASE)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def interpret(
        self, message: str, today: date | None = None
    ) -> StructuredIntent:
        today = today or datetime.now(ZoneInfo(self.settings.beacon_timezone)).date()
        compact = " ".join(message.strip().split())
        lowered = compact.casefold()
        target_date = self._target_date(lowered, today)

        is_schedule = bool(re.match(r"^(please\s+)?schedule\b", lowered))
        if not is_schedule and self._is_brief_request(lowered):
            return StructuredIntent(intent=IntentType.BRIEF, deadline=target_date)

        is_create = bool(
            re.match(r"^(please\s+)?(?:add|create|remember|buy)\b", lowered)
        )
        if not is_schedule and is_create:
            title = re.sub(r"^please\s+", "", compact, flags=re.IGNORECASE)
            if not re.match(r"^buy\b", title, flags=re.IGNORECASE):
                title = re.sub(
                    r"^(?:(?:add|create)(?:\s+a)?(?:\s+task)?(?:\s+to)?\s+|remember\s+to\s+)",
                    "",
                    title,
                    flags=re.IGNORECASE,
                )
            title = re.sub(
                r"\b(?:(?:by|on)\s+)?(?:today|tomorrow)\b",
                "",
                title,
                flags=re.IGNORECASE,
            )
            title = " ".join(title.strip(" .,!?").split())
            if not title:
                raise UnsupportedIntentError("Name the task you want Beacon to create.")
            return StructuredIntent(
                intent=IntentType.CREATE_TASK,
                title=title,
                deadline=target_date,
            )

        if not is_schedule:
            raise UnsupportedIntentError(
                "I can currently create a task, produce a brief, or schedule work."
            )

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

    def _extract_duration(self, message: str) -> tuple[int, str]:
        match = self._duration.search(message)
        if not match:
            return self.settings.beacon_interaction_default_duration_minutes, message
        amount = float(match.group(1))
        unit = match.group(2).casefold()
        minutes = round(amount * 60) if unit.startswith("h") else round(amount)
        if not 1 <= minutes <= 1440:
            raise UnsupportedIntentError(
                "Work block duration must be between 1 minute and 24 hours."
            )
        return minutes, (message[: match.start()] + message[match.end() :])

    @staticmethod
    def _target_date(message: str, today: date) -> date | None:
        if re.search(r"\btomorrow\b", message):
            return today + timedelta(days=1)
        if re.search(r"\btoday\b", message):
            return today
        return None

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
