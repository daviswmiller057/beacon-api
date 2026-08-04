from datetime import date
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.conversation.models import ToolDeclaration
from app.context.domain import ContextOperation, EntityInput, EntityType, Provenance
from app.models import DailyEventRange, IntentType, StructuredIntent


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateTaskArguments(ToolArguments):
    title: str = Field(min_length=1, max_length=500, description="Task title")
    deadline: date | None = Field(default=None, description="ISO due date")


class ScheduleTaskArguments(ToolArguments):
    task_id: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    deadline: date | None = None
    time_constraint: str | None = Field(default=None, min_length=1, max_length=100)
    duration_minutes: int | None = Field(default=None, gt=0, le=1440)

    @model_validator(mode="after")
    def one_selector(self):
        if sum((self.task_id is not None, bool(self.title))) != 1:
            raise ValueError("exactly_one_task_selector_required")
        return self


class CreateCalendarEventsArguments(ToolArguments):
    title: str = Field(min_length=1, max_length=500)
    daily_event_range: DailyEventRange
    description: str = Field(default="", max_length=5000)
    calendar_name: str | None = Field(default=None, min_length=1, max_length=200)
    source_reference: str | None = Field(default=None, min_length=1, max_length=500)


class BriefArguments(ToolArguments):
    deadline: date | None = None


class ClarificationArguments(ToolArguments):
    question: str = Field(
        min_length=1,
        max_length=500,
        description="The minimum question needed to complete a Beacon request",
    )


class AddAliasArguments(ToolArguments):
    entity_type: EntityType
    canonical_name: str = Field(min_length=1, max_length=500)
    alias: str = Field(min_length=1, max_length=500)


class AddFactArguments(ToolArguments):
    entity_type: EntityType
    canonical_name: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=200)
    value: Any
    replace_existing: bool = False


class AddRelationshipArguments(ToolArguments):
    source_type: EntityType
    source_name: str = Field(min_length=1, max_length=500)
    relationship: str = Field(min_length=1, max_length=200)
    target_type: EntityType
    target_name: str = Field(min_length=1, max_length=500)


class QueryContextArguments(ToolArguments):
    entity_reference: str = Field(min_length=1, max_length=500)


class ForgetContextArguments(ToolArguments):
    operation: Literal[
        "DEPRECATE_ALIAS", "DEPRECATE_FACT", "DEPRECATE_RELATIONSHIP"
    ]
    entity_reference: str = Field(min_length=1, max_length=500)
    alias: str | None = Field(default=None, min_length=1, max_length=500)
    predicate: str | None = Field(default=None, min_length=1, max_length=200)
    value_reference: str | None = Field(default=None, min_length=1, max_length=1000)
    relationship: str | None = Field(default=None, min_length=1, max_length=200)
    target_reference: str | None = Field(default=None, min_length=1, max_length=500)


class RegisteredTool:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        arguments: type[ToolArguments],
        mapper: Callable[[ToolArguments], StructuredIntent],
        read_only: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.arguments = arguments
        self.mapper = mapper
        self.read_only = read_only

    def declaration(self) -> ToolDeclaration:
        return ToolDeclaration(
            name=self.name,
            description=self.description,
            parameters=self.arguments.model_json_schema(mode="serialization"),
            read_only=self.read_only,
        )

    def intent(self, raw_arguments: dict[str, Any]) -> StructuredIntent:
        return self.mapper(self.arguments.model_validate(raw_arguments))

    def validate(
        self, raw_arguments: dict[str, Any]
    ) -> tuple[StructuredIntent, dict[str, Any]]:
        validated = self.arguments.model_validate(raw_arguments)
        return self.mapper(validated), validated.model_dump(mode="json")


def _create_task(value: ToolArguments) -> StructuredIntent:
    args = CreateTaskArguments.model_validate(value)
    return StructuredIntent(intent=IntentType.CREATE_TASK, **args.model_dump())


def _schedule_task(value: ToolArguments) -> StructuredIntent:
    args = ScheduleTaskArguments.model_validate(value)
    return StructuredIntent(intent=IntentType.SCHEDULE_TASK, **args.model_dump())


def _calendar_events(value: ToolArguments) -> StructuredIntent:
    args = CreateCalendarEventsArguments.model_validate(value)
    return StructuredIntent(
        intent=IntentType.CREATE_CALENDAR_EVENTS, **args.model_dump()
    )


def _brief(value: ToolArguments) -> StructuredIntent:
    args = BriefArguments.model_validate(value)
    return StructuredIntent(intent=IntentType.BRIEF, **args.model_dump())


def _clarification(value: ToolArguments) -> StructuredIntent:
    args = ClarificationArguments.model_validate(value)
    return StructuredIntent(
        intent=IntentType.UNKNOWN, clarification_question=args.question
    )


def _alias(value: ToolArguments) -> StructuredIntent:
    args = AddAliasArguments.model_validate(value)
    return StructuredIntent(
        intent=IntentType.STORE_CONTEXT,
        operation=ContextOperation.ADD_ALIAS,
        entity=EntityInput(type=args.entity_type, canonical_name=args.canonical_name),
        alias=args.alias,
        provenance=Provenance.EXPLICIT_USER_STATEMENT,
    )


def _fact(value: ToolArguments) -> StructuredIntent:
    args = AddFactArguments.model_validate(value)
    return StructuredIntent(
        intent=IntentType.STORE_CONTEXT,
        operation=ContextOperation.ADD_FACT,
        entity=EntityInput(type=args.entity_type, canonical_name=args.canonical_name),
        predicate=args.predicate,
        value=args.value,
        replace_existing=args.replace_existing,
        provenance=Provenance.EXPLICIT_USER_STATEMENT,
    )


def _relationship(value: ToolArguments) -> StructuredIntent:
    args = AddRelationshipArguments.model_validate(value)
    return StructuredIntent(
        intent=IntentType.STORE_CONTEXT,
        operation=ContextOperation.ADD_RELATIONSHIP,
        source_entity=EntityInput(type=args.source_type, canonical_name=args.source_name),
        relationship=args.relationship,
        target_entity=EntityInput(type=args.target_type, canonical_name=args.target_name),
        provenance=Provenance.EXPLICIT_USER_STATEMENT,
    )


def _query(value: ToolArguments) -> StructuredIntent:
    args = QueryContextArguments.model_validate(value)
    return StructuredIntent(
        intent=IntentType.QUERY_CONTEXT,
        operation=ContextOperation.QUERY_ENTITY,
        entity_reference=args.entity_reference,
    )


def _forget(value: ToolArguments) -> StructuredIntent:
    args = ForgetContextArguments.model_validate(value)
    return StructuredIntent(
        intent=IntentType.FORGET_CONTEXT,
        operation=ContextOperation(args.operation),
        entity_reference=args.entity_reference,
        alias=args.alias,
        predicate=args.predicate,
        value_reference=args.value_reference,
        relationship=args.relationship,
        target_reference=args.target_reference,
    )


class BeaconToolRegistry:
    def __init__(self) -> None:
        tools = [
            RegisteredTool(
                name="create_task",
                description="Create one Beacon task.",
                arguments=CreateTaskArguments,
                mapper=_create_task,
            ),
            RegisteredTool(
                name="schedule_task",
                description="Schedule work for one existing or named task.",
                arguments=ScheduleTaskArguments,
                mapper=_schedule_task,
            ),
            RegisteredTool(
                name="create_calendar_events",
                description=(
                    "Create fixed-time daily calendar events over one inclusive "
                    "bounded date range."
                ),
                arguments=CreateCalendarEventsArguments,
                mapper=_calendar_events,
            ),
            RegisteredTool(
                name="request_brief",
                description="Request Beacon's read-only daily brief.",
                arguments=BriefArguments,
                mapper=_brief,
                read_only=True,
            ),
            RegisteredTool(
                name="request_clarification",
                description=(
                    "Ask for the minimum missing information needed to form a "
                    "complete Beacon request."
                ),
                arguments=ClarificationArguments,
                mapper=_clarification,
                read_only=True,
            ),
            RegisteredTool(
                name="add_context_alias",
                description="Store an explicit alias for a context entity.",
                arguments=AddAliasArguments,
                mapper=_alias,
            ),
            RegisteredTool(
                name="add_context_fact",
                description="Store or explicitly correct one context fact.",
                arguments=AddFactArguments,
                mapper=_fact,
            ),
            RegisteredTool(
                name="add_context_relationship",
                description="Store a relationship between independent context entities.",
                arguments=AddRelationshipArguments,
                mapper=_relationship,
            ),
            RegisteredTool(
                name="query_context",
                description="Query active context for one entity reference.",
                arguments=QueryContextArguments,
                mapper=_query,
                read_only=True,
            ),
            RegisteredTool(
                name="forget_context",
                description="Deprecate one specific alias, fact, or relationship.",
                arguments=ForgetContextArguments,
                mapper=_forget,
            ),
        ]
        self._tools = {tool.name: tool for tool in tools}

    def declarations(self) -> list[ToolDeclaration]:
        return [tool.declaration() for tool in self._tools.values()]

    def capabilities(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def to_intent(self, name: str, arguments: dict[str, Any]) -> StructuredIntent:
        tool = self.get(name)
        if tool is None:
            raise KeyError(name)
        return tool.intent(arguments)

    def validate(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[StructuredIntent, dict[str, Any]]:
        tool = self.get(name)
        if tool is None:
            raise KeyError(name)
        return tool.validate(arguments)
