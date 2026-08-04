import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.intake.errors import UnsupportedIntentError
from app.context.domain import ContextOperation, EntityInput, EntityType, Provenance
from app.models import IntentType, StructuredIntent


class RuleBasedIntentInterpreter:
    """Small, deterministic fallback for Beacon's minimum useful commands."""

    _duration = re.compile(
        r"\bfor\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\b",
        re.IGNORECASE,
    )
    _task_id = re.compile(r"(?:\btask\s+|#)(\d+)\b", re.IGNORECASE)
    _month = (
        r"January|February|March|April|May|June|July|August|"
        r"September|October|November|December"
    )
    _daily_event_range = re.compile(
        rf"^(?:please\s+)?(?:schedule\s+|add\s+|create\s+)?"
        rf"(?P<title>.+?)\s+(?P<start_month>{_month})\s+"
        rf"(?P<start_day>\d{{1,2}})"
        rf"(?:\s+through\s+(?:(?P<end_month>{_month})\s+)?"
        rf"(?P<end_day>\d{{1,2}}))?,\s*(?P<year>\d{{4}}),?\s+"
        rf"from\s+(?P<start_time>\d{{1,2}}(?::\d{{2}})?\s*[AP]M)\s+"
        rf"to\s+(?P<end_time>\d{{1,2}}(?::\d{{2}})?\s*[AP]M)"
        rf"(?:\s+each\s+day)?[.!?]?$",
        re.IGNORECASE,
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def interpret(
        self, message: str, today: date | None = None
    ) -> StructuredIntent:
        today = today or datetime.now(ZoneInfo(self.settings.beacon_timezone)).date()
        compact = " ".join(message.strip().split())
        lowered = compact.casefold()
        calendar_intent = self._calendar_event_intent(compact)
        if calendar_intent is not None:
            return calendar_intent
        context_intent = self._context_intent(compact)
        if context_intent is not None:
            return context_intent
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

    def _calendar_event_intent(self, message: str) -> StructuredIntent | None:
        match = self._daily_event_range.match(message)
        if not match:
            return None
        year = int(match.group("year"))
        start_month = match.group("start_month")
        end_month = match.group("end_month") or start_month
        start_date = datetime.strptime(
            f"{start_month} {match.group('start_day')} {year}", "%B %d %Y"
        ).date()
        end_date = datetime.strptime(
            f"{end_month} {match.group('end_day') or match.group('start_day')} {year}",
            "%B %d %Y",
        ).date()
        return StructuredIntent(
            intent=IntentType.CREATE_CALENDAR_EVENTS,
            title=" ".join(match.group("title").strip().split()),
            daily_event_range={
                "start_date": start_date,
                "end_date": end_date,
                "daily_start_time": self._parse_clock(match.group("start_time")),
                "daily_end_time": self._parse_clock(match.group("end_time")),
                "repeat_daily": True,
            },
        )

    @staticmethod
    def _parse_clock(value: str):
        compact = " ".join(value.upper().split())
        pattern = "%I:%M %p" if ":" in compact else "%I %p"
        return datetime.strptime(compact, pattern).time()

    def _context_intent(self, message: str) -> StructuredIntent | None:
        text = message.strip(" .!?\t\n")

        query = re.match(
            r"^(?:what does beacon know|what do you know|what have you remembered) about\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if query:
            return StructuredIntent(
                intent=IntentType.QUERY_CONTEXT,
                operation=ContextOperation.QUERY_ENTITY,
                entity_reference=self._clean_reference(query.group(1)),
            )

        forget_alias = re.match(
            r"^forget (?:the )?alias\s+[\"“]?(.+?)[\"”]?\s+for\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if forget_alias:
            return StructuredIntent(
                intent=IntentType.FORGET_CONTEXT,
                operation=ContextOperation.DEPRECATE_ALIAS,
                alias=self._clean_reference(forget_alias.group(1)),
                entity_reference=self._clean_reference(forget_alias.group(2)),
            )

        forget_relationship = re.match(
            r"^forget (?:that )?(.+?)\s+(normally operates at|rehearses at|works at|located at)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if forget_relationship:
            return StructuredIntent(
                intent=IntentType.FORGET_CONTEXT,
                operation=ContextOperation.DEPRECATE_RELATIONSHIP,
                entity_reference=self._clean_reference(forget_relationship.group(1)),
                relationship=self._relationship_key(forget_relationship.group(2)),
                target_reference=self._clean_reference(forget_relationship.group(3)),
            )

        forget_fact = re.match(
            r"^forget\s+(.+?)[’']s\s+(?:old\s+)?(.+?)(?:\s+(?:value|fact))?$",
            text,
            re.IGNORECASE,
        )
        if forget_fact:
            predicate = self._fact_key(forget_fact.group(2))
            return StructuredIntent(
                intent=IntentType.FORGET_CONTEXT,
                operation=ContextOperation.DEPRECATE_FACT,
                entity_reference=self._clean_reference(forget_fact.group(1)),
                predicate=predicate,
            )

        when_i_say = re.match(
            r"^when i say\s+[\"“](.+?)[\"”],?\s+i mean\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        alias_teaching = when_i_say or re.match(
            r"^remember that\s+[\"“]?(.+?)[\"”]?\s+means\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if alias_teaching:
            alias = self._clean_reference(alias_teaching.group(1))
            canonical = self._clean_reference(alias_teaching.group(2))
            return StructuredIntent(
                intent=IntentType.STORE_CONTEXT,
                operation=ContextOperation.ADD_ALIAS,
                entity=EntityInput(
                    type=self._infer_entity_type(canonical),
                    canonical_name=canonical,
                ),
                alias=alias,
                provenance=Provenance.EXPLICIT_USER_STATEMENT,
            )

        relationship = re.match(
            r"^(?:remember that\s+)?(.+?)\s+(normally operates at|rehearses at|works at)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if relationship:
            source_name = self._clean_reference(relationship.group(1))
            target_name = self._clean_reference(relationship.group(3))
            return StructuredIntent(
                intent=IntentType.STORE_CONTEXT,
                operation=ContextOperation.ADD_RELATIONSHIP,
                source_entity=EntityInput(
                    type=self._infer_entity_type(source_name, source=True),
                    canonical_name=source_name,
                ),
                relationship=self._relationship_key(relationship.group(2)),
                target_entity=EntityInput(
                    type=self._infer_entity_type(target_name, target=True),
                    canonical_name=target_name,
                ),
                provenance=Provenance.EXPLICIT_USER_STATEMENT,
            )

        correction = re.match(
            r"^correct\s+(.+?)[’']s\s+(.+?)\s+to\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if correction:
            name = self._clean_reference(correction.group(1))
            return StructuredIntent(
                intent=IntentType.STORE_CONTEXT,
                operation=ContextOperation.ADD_FACT,
                entity=EntityInput(type=self._infer_entity_type(name), canonical_name=name),
                predicate=self._fact_key(correction.group(2)),
                value=self._unquote(correction.group(3)),
                provenance=Provenance.EXPLICIT_USER_STATEMENT,
                replace_existing=True,
            )

        possessive_fact = re.match(
            r"^(?:remember that\s+)?(.+?)[’']s\s+(.+?)\s+is\s+(?:at\s+)?(.+)$",
            text,
            re.IGNORECASE,
        )
        if possessive_fact:
            name = self._clean_reference(possessive_fact.group(1))
            return self._fact_intent(name, possessive_fact.group(2), possessive_fact.group(3))

        note_fact = re.match(
            r"^(?:remember that\s+)?(.+?)\s+has\s+(?:the\s+)?(?:note|parking note)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if note_fact:
            name = self._clean_reference(note_fact.group(1))
            predicate = "parking_note" if "parking note" in text.casefold() else "note"
            return self._fact_intent(name, predicate, note_fact.group(2))

        personal_fact = re.match(
            r"^(?:remember that\s+)?(.+?)\s+is\s+my\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if personal_fact:
            name = self._clean_reference(personal_fact.group(1))
            return StructuredIntent(
                intent=IntentType.STORE_CONTEXT,
                operation=ContextOperation.ADD_FACT,
                entity=EntityInput(type=EntityType.PERSON, canonical_name=name),
                predicate="relationship_to_user",
                value=self._unquote(personal_fact.group(2)),
                provenance=Provenance.EXPLICIT_USER_STATEMENT,
            )
        return None

    def _fact_intent(self, name: str, predicate: str, value: str) -> StructuredIntent:
        return StructuredIntent(
            intent=IntentType.STORE_CONTEXT,
            operation=ContextOperation.ADD_FACT,
            entity=EntityInput(type=self._infer_entity_type(name), canonical_name=name),
            predicate=self._fact_key(predicate),
            value=self._unquote(value),
            provenance=Provenance.EXPLICIT_USER_STATEMENT,
        )

    @staticmethod
    def _clean_reference(value: str) -> str:
        return RuleBasedIntentInterpreter._unquote(value).strip(" ,")

    @staticmethod
    def _unquote(value: str) -> str:
        return value.strip().strip('"“”').strip()

    @staticmethod
    def _relationship_key(value: str) -> str:
        return "_".join(value.casefold().split())

    @staticmethod
    def _fact_key(value: str) -> str:
        normalized = "_".join(re.sub(r"[^\w]+", " ", value.casefold()).split())
        aliases = {
            "office": "office_address",
            "office_address": "office_address",
            "address": "address",
        }
        return aliases.get(normalized, normalized)

    @staticmethod
    def _infer_entity_type(
        name: str, *, source: bool = False, target: bool = False
    ) -> EntityType:
        lowered = name.casefold()
        if re.match(r"^(?:dr|doctor|mr|mrs|ms|prof)\.?\s", lowered):
            return EntityType.PERSON
        if any(word in lowered for word in ("theater", "hall", "house", "auditorium")):
            return EntityType.VENUE
        if any(word in lowered for word in ("players", "company", "organization", "theatre")):
            return EntityType.ORGANIZATION
        if target:
            return EntityType.VENUE
        if source:
            return EntityType.ORGANIZATION
        return EntityType.CONCEPT

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
