from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.context.domain import (
    ContextMutationResult,
    ContextOperation,
    EntityContextResult,
    EntityInput,
    Provenance,
)


class AvailabilityRequest(BaseModel):
    earliest_iso: datetime
    deadline_iso: datetime
    duration_minutes: Annotated[int, Field(gt=0, le=1440)]
    buffer_before_minutes: Annotated[int, Field(ge=0, le=720)] = 0
    buffer_after_minutes: Annotated[int, Field(ge=0, le=720)] = 0
    max_options: Annotated[int, Field(ge=1, le=20)] = 3
    calendar_names: list[str] | None = None
    daily_start: str = "09:00"
    daily_end: str = "22:00"

    @model_validator(mode="after")
    def validate_range(self):
        if self.deadline_iso <= self.earliest_iso:
            raise ValueError("deadline_iso must be after earliest_iso")
        return self


class BusyInterval(BaseModel):
    start_iso: datetime
    end_iso: datetime
    calendar: str
    title: str | None = None


class AvailabilityOption(BaseModel):
    start_iso: datetime
    end_iso: datetime
    score: float
    reasons: list[str]


class AvailabilityResponse(BaseModel):
    calendars_checked: list[str]
    events_found: int
    options: list[AvailabilityOption]
    no_availability: bool


class VikunjaTask(BaseModel):
    id: int
    title: str
    description: str = ""
    due_date: datetime | None = None
    priority: int = 0
    done: bool = False
    project_id: int | None = None
    labels: list[dict[str, Any]] = Field(default_factory=list)


class ScheduleTaskRequest(BaseModel):
    duration_minutes: Annotated[int, Field(gt=0, le=1440)]

    earliest_iso: datetime | None = None
    deadline_iso: datetime | None = None

    calendar_name: str | None = None
    availability_calendars: list[str] | None = None

    daily_start: str = "09:00"
    daily_end: str = "22:00"

    buffer_before_minutes: Annotated[int, Field(ge=0, le=720)] = 15
    buffer_after_minutes: Annotated[int, Field(ge=0, le=720)] = 15

    create_event: bool = True


class CalendarEventResult(BaseModel):
    uid: str | None = None
    href: str | None = None
    calendar: str
    title: str
    start_iso: datetime
    end_iso: datetime


class ScheduleStatus(StrEnum):
    NEW = "NEW"
    UNCHANGED = "UNCHANGED"
    UPDATED = "UPDATED"
    RECOMMENDATION_ONLY = "RECOMMENDATION_ONLY"


class ScheduleTaskResponse(BaseModel):
    status: ScheduleStatus
    task: VikunjaTask
    selected_option: AvailabilityOption
    calendars_checked: list[str]
    events_found: int
    calendar_event: CalendarEventResult | None = None
    already_scheduled: bool = False


class BriefCalendarEvent(BaseModel):
    uid: str | None = None
    calendar: str
    title: str
    description: str = ""
    location: str | None = None
    start_iso: datetime
    end_iso: datetime
    all_day: bool = False
    is_beacon_work_block: bool = False
    vikunja_task_id: int | None = None


class DailyBriefCalendar(BaseModel):
    events: list[BriefCalendarEvent]
    work_blocks: list[BriefCalendarEvent]


class DailyBriefTasks(BaseModel):
    overdue: list[VikunjaTask]
    due_today: list[VikunjaTask]
    highest_priority: VikunjaTask | None = None


class TravelEstimate(BaseModel):
    event_uid: str | None = None
    event_title: str
    origin: str
    destination: str
    duration_minutes: float
    distance_kilometers: float
    buffer_minutes: int
    leave_by: datetime


class WeatherConditions(BaseModel):
    entity_id: str
    condition: str
    temperature: float | None = None
    temperature_unit: str | None = None
    humidity: float | None = None
    observed_at: datetime | None = None


class BriefWarningSource(StrEnum):
    CALENDAR = "CALENDAR"
    VIKUNJA = "VIKUNJA"
    WAZE = "WAZE"
    HOME_ASSISTANT = "HOME_ASSISTANT"


class BriefWarning(BaseModel):
    source: BriefWarningSource
    code: str
    message: str


class BriefConflictType(StrEnum):
    OVERLAPPING_EVENTS = "OVERLAPPING_EVENTS"
    WORK_BLOCK_OVERLAP = "WORK_BLOCK_OVERLAP"
    INSUFFICIENT_TRAVEL_TIME = "INSUFFICIENT_TRAVEL_TIME"
    LEAVE_BY_PASSED = "LEAVE_BY_PASSED"


class BriefConflict(BaseModel):
    type: BriefConflictType
    message: str
    event_uids: list[str] = Field(default_factory=list)


class DailyBriefSummary(BaseModel):
    event_count: int
    work_block_count: int
    overdue_task_count: int
    due_today_task_count: int
    conflict_count: int
    next_event: BriefCalendarEvent | None = None
    highest_priority_task: VikunjaTask | None = None


class DailyBriefResponse(BaseModel):
    date: date
    timezone: str
    generated_at: datetime
    calendar: DailyBriefCalendar
    tasks: DailyBriefTasks
    travel: list[TravelEstimate]
    weather: WeatherConditions | None = None
    warnings: list[BriefWarning]
    conflicts: list[BriefConflict]
    summary: DailyBriefSummary
    spoken_summary: str


class IntentType(StrEnum):
    BRIEF = "BRIEF"
    CREATE_TASK = "CREATE_TASK"
    SCHEDULE_TASK = "SCHEDULE_TASK"
    STORE_CONTEXT = "STORE_CONTEXT"
    QUERY_CONTEXT = "QUERY_CONTEXT"
    FORGET_CONTEXT = "FORGET_CONTEXT"
    UNKNOWN = "UNKNOWN"


class StructuredIntent(BaseModel):
    """Provider-neutral description of what the user wants.

    The validation aliases keep the pre-intake API contract readable while new
    responses use intent-language rather than execution-language.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    intent: IntentType = Field(validation_alias=AliasChoices("intent", "action"))
    task_id: int | None = None
    title: Annotated[str, Field(min_length=1, max_length=500)] | None = Field(
        default=None,
        validation_alias=AliasChoices("title", "task_title"),
    )
    deadline: date | None = Field(
        default=None,
        validation_alias=AliasChoices("deadline", "target_date"),
    )
    time_constraint: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    duration_minutes: Annotated[int, Field(gt=0, le=1440)] | None = None
    clarification_question: Annotated[
        str, Field(min_length=1, max_length=500)
    ] | None = None
    operation: ContextOperation | None = None
    entity: EntityInput | None = None
    source_entity: EntityInput | None = None
    target_entity: EntityInput | None = None
    entity_reference: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    target_reference: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    alias: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    predicate: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    value: Any | None = None
    value_reference: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    relationship: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    provenance: Provenance | None = None
    source_reference: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    replace_existing: bool = False
    # Compatibility for existing callers. New interpreters never emit it.
    create_event: bool = Field(default=True, exclude=True)

    @model_validator(mode="after")
    def validate_intent_fields(self):
        if self.intent is IntentType.SCHEDULE_TASK:
            selectors = [self.task_id is not None, bool(self.title)]
            if sum(selectors) != 1:
                raise ValueError(
                    "SCHEDULE_TASK requires exactly one of task_id or title"
                )
        if self.intent is IntentType.CREATE_TASK and not self.title:
            raise ValueError("CREATE_TASK requires title")
        if self.intent is IntentType.UNKNOWN and not self.clarification_question:
            raise ValueError("UNKNOWN requires clarification_question")
        if self.intent is IntentType.QUERY_CONTEXT:
            if self.operation not in (None, ContextOperation.QUERY_ENTITY):
                raise ValueError("QUERY_CONTEXT only supports QUERY_ENTITY")
            if not self.entity_reference:
                raise ValueError("QUERY_CONTEXT requires entity_reference")
            self.operation = ContextOperation.QUERY_ENTITY
        if self.intent is IntentType.STORE_CONTEXT:
            if self.operation not in {
                ContextOperation.CREATE_ENTITY,
                ContextOperation.ADD_ALIAS,
                ContextOperation.ADD_FACT,
                ContextOperation.ADD_RELATIONSHIP,
            }:
                raise ValueError("STORE_CONTEXT requires a supported store operation")
            if self.operation is ContextOperation.CREATE_ENTITY and not self.entity:
                raise ValueError("CREATE_ENTITY requires entity")
            if self.operation is ContextOperation.ADD_ALIAS and not (self.entity and self.alias):
                raise ValueError("ADD_ALIAS requires entity and alias")
            if self.operation is ContextOperation.ADD_FACT and not (
                self.entity and self.predicate and self.value is not None
            ):
                raise ValueError("ADD_FACT requires entity, predicate, and value")
            if self.operation is ContextOperation.ADD_RELATIONSHIP and not (
                self.source_entity and self.relationship and self.target_entity
            ):
                raise ValueError(
                    "ADD_RELATIONSHIP requires source_entity, relationship, and target_entity"
                )
            self.provenance = self.provenance or Provenance.EXPLICIT_USER_STATEMENT
        if self.intent is IntentType.FORGET_CONTEXT:
            if self.operation not in {
                ContextOperation.DEPRECATE_ALIAS,
                ContextOperation.DEPRECATE_FACT,
                ContextOperation.DEPRECATE_RELATIONSHIP,
            }:
                raise ValueError("FORGET_CONTEXT requires a supported deprecation operation")
            if not self.entity_reference:
                raise ValueError("FORGET_CONTEXT requires entity_reference")
            if self.operation is ContextOperation.DEPRECATE_ALIAS and not self.alias:
                raise ValueError("DEPRECATE_ALIAS requires alias")
            if self.operation is ContextOperation.DEPRECATE_FACT and not self.predicate:
                raise ValueError("DEPRECATE_FACT requires predicate")
            if self.operation is ContextOperation.DEPRECATE_RELATIONSHIP and not self.relationship:
                raise ValueError("DEPRECATE_RELATIONSHIP requires relationship")
        return self

    @property
    def action(self) -> IntentType:
        """Deprecated compatibility alias for integrations using v0.3."""
        return self.intent

    @property
    def task_title(self) -> str | None:
        return self.title

    @property
    def target_date(self) -> date | None:
        return self.deadline


class ActionType(StrEnum):
    CREATE_TASK = "CREATE_TASK"
    SCHEDULE_WORK_BLOCK = "SCHEDULE_WORK_BLOCK"
    GENERATE_BRIEF = "GENERATE_BRIEF"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    QUERY_CONTEXT = "QUERY_CONTEXT"
    MUTATE_CONTEXT = "MUTATE_CONTEXT"


class PlannedAction(BaseModel):
    action: ActionType
    title: str | None = None
    task_id: int | None = None
    deadline: date | None = None
    window_start: str | None = None
    window_end: str | None = None
    duration_minutes: Annotated[int, Field(gt=0, le=1440)] | None = None
    create_event: bool = True
    reuse_existing: bool = False
    question: str | None = None
    context_operation: ContextOperation | None = None
    entity: EntityInput | None = None
    source_entity: EntityInput | None = None
    target_entity: EntityInput | None = None
    entity_reference: str | None = None
    target_reference: str | None = None
    alias: str | None = None
    predicate: str | None = None
    value: Any | None = None
    value_reference: str | None = None
    relationship: str | None = None
    provenance: Provenance | None = None
    source_reference: str | None = None
    replace_existing: bool = False


class ActionPlan(BaseModel):
    intent: StructuredIntent
    actions: list[PlannedAction]


class InteractRequest(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    intent: StructuredIntent | None = None

    @model_validator(mode="after")
    def validate_input(self):
        if self.message is None and self.intent is None:
            raise ValueError("Provide message or intent")
        return self


class InteractionAction(BaseModel):
    action: str
    status: str
    target: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class InteractResponse(BaseModel):
    result: str
    intent: StructuredIntent
    plan: ActionPlan | None = None
    actions_taken: list[InteractionAction]
    brief: DailyBriefResponse | None = None
    schedule: ScheduleTaskResponse | None = None
    task: VikunjaTask | None = None
    context: EntityContextResult | ContextMutationResult | None = None


class ServiceStatusResponse(BaseModel):
    status: str
    service: str
    version: str
    timezone: str
    calendars: list[str]
    schedule_calendar: str
    integrations: dict[str, bool]
    interaction_modes: list[str]
