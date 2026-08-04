import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.context.domain import (
    ContextMutationResult,
    ContextOperation,
    EntityContextResult,
    MutationStatus,
    Provenance,
    ResolutionStatus,
)
from app.context.service import ContextRegistryService
from app.intake.errors import (
    AmbiguousTaskError,
    InteractionError,
    InteractionTaskNotFound,
)
from app.models import (
    ActionPlan,
    ActionType,
    DailyBriefResponse,
    InteractResponse,
    InteractionAction,
    PlannedAction,
    ScheduleTaskRequest,
    ScheduleTaskResponse,
    VikunjaTask,
)
from app.services.daily_brief import DailyBriefService
from app.services.scheduler import SchedulerService
from app.services.vikunja_client import VikunjaClient


class ActionExecutor:
    """Execute only operations already authorized by an ActionPlan."""

    def __init__(
        self,
        *,
        vikunja: VikunjaClient,
        scheduler: SchedulerService,
        daily_brief: DailyBriefService,
        context_registry: ContextRegistryService | None = None,
    ) -> None:
        self.vikunja = vikunja
        self.scheduler = scheduler
        self.daily_brief = daily_brief
        self.context_registry = context_registry

    def execute(
        self,
        plan: ActionPlan,
        now: datetime,
        timezone: ZoneInfo,
    ) -> InteractResponse:
        actions_taken: list[InteractionAction] = []
        current_task: VikunjaTask | None = None
        brief: DailyBriefResponse | None = None
        scheduled: ScheduleTaskResponse | None = None
        context_result: EntityContextResult | ContextMutationResult | None = None

        for action in plan.actions:
            if action.action is ActionType.REQUEST_CLARIFICATION:
                question = action.question or "Could you clarify what you want Beacon to do?"
                return InteractResponse(
                    result=question,
                    intent=plan.intent,
                    plan=plan,
                    actions_taken=[
                        InteractionAction(
                            action="clarification_requested", status="PENDING"
                        )
                    ],
                )
            if action.action is ActionType.GENERATE_BRIEF:
                brief = self.daily_brief.build(action.deadline)
                actions_taken.append(self._brief_audit(brief))
                continue
            if action.action is ActionType.QUERY_CONTEXT:
                context_result = self._query_context(action)
                actions_taken.append(
                    InteractionAction(
                        action="context_queried",
                        status="READ_ONLY",
                        target=action.entity_reference,
                    )
                )
                continue
            if action.action is ActionType.MUTATE_CONTEXT:
                context_result = self._mutate_context(action)
                actions_taken.append(
                    InteractionAction(
                        action=(action.context_operation.value.lower() if action.context_operation else "context_mutation"),
                        status=context_result.status.value.upper(),
                        target=(context_result.entity.canonical_name if context_result.entity else action.entity_reference),
                    )
                )
                continue
            if action.action is ActionType.CREATE_TASK:
                current_task, audit = self._create_or_resolve(action, timezone)
                if audit is not None:
                    actions_taken.append(audit)
                continue
            if current_task is None:
                if action.task_id is None:
                    raise InteractionError("Schedule action has no task")
                current_task = self.vikunja.get_task(action.task_id)
            scheduled = self._schedule(current_task, action, now, timezone)
            actions_taken.append(self._schedule_audit(current_task, scheduled))

        result = self._result(brief, scheduled, current_task, timezone, context_result)
        return InteractResponse(
            result=result,
            intent=plan.intent,
            plan=plan,
            actions_taken=actions_taken,
            brief=brief,
            schedule=scheduled,
            task=current_task,
            context=context_result,
        )

    def _context_service(self) -> ContextRegistryService:
        if self.context_registry is None:
            raise InteractionError("Context Registry service is unavailable")
        return self.context_registry

    def _query_context(self, action: PlannedAction) -> EntityContextResult:
        if not action.entity_reference:
            raise InteractionError("Context query has no entity reference")
        return self._context_service().query_entity(action.entity_reference)

    def _mutate_context(self, action: PlannedAction) -> ContextMutationResult:
        service = self._context_service()
        operation = action.context_operation
        provenance = action.provenance or Provenance.EXPLICIT_USER_STATEMENT
        if operation is ContextOperation.CREATE_ENTITY and action.entity:
            return service.create_entity(action.entity)
        if operation is ContextOperation.ADD_ALIAS and action.entity and action.alias:
            return service.add_alias(action.entity, action.alias, provenance, action.source_reference)
        if operation is ContextOperation.ADD_FACT and action.entity and action.predicate and action.value is not None:
            return service.add_fact(
                action.entity,
                action.predicate,
                action.value,
                provenance,
                action.source_reference,
                replace_existing=action.replace_existing,
            )
        if (
            operation is ContextOperation.ADD_RELATIONSHIP
            and action.source_entity
            and action.relationship
            and action.target_entity
        ):
            return service.add_relationship(
                action.source_entity,
                action.relationship,
                action.target_entity,
                provenance,
                action.source_reference,
            )
        if operation is ContextOperation.DEPRECATE_ALIAS and action.entity_reference and action.alias:
            return service.deprecate_alias(action.entity_reference, action.alias)
        if operation is ContextOperation.DEPRECATE_FACT and action.entity_reference and action.predicate:
            return service.deprecate_fact(action.entity_reference, action.predicate, action.value_reference)
        if operation is ContextOperation.DEPRECATE_RELATIONSHIP and action.entity_reference and action.relationship:
            return service.deprecate_relationship(action.entity_reference, action.relationship, action.target_reference)
        raise InteractionError("Context mutation is incomplete")

    def _create_or_resolve(
        self, action: PlannedAction, timezone: ZoneInfo
    ) -> tuple[VikunjaTask, InteractionAction | None]:
        task = self._find_existing(action.title or "") if action.reuse_existing else None
        if task is None:
            if action.reuse_existing and action.deadline is None:
                raise InteractionTaskNotFound(
                    f'No incomplete Vikunja task matched "{action.title}"; '
                    "provide a date before Beacon creates and schedules it."
                )
            task = self.vikunja.create_task(
                action.title or "", self._due_datetime(action.deadline, timezone)
            )
            audit = InteractionAction(
                action="task_created",
                status="CREATED",
                target=f"vikunja-task:{task.id}",
            )
        else:
            audit = None
        return task, audit

    def _schedule(
        self,
        task: VikunjaTask,
        action: PlannedAction,
        now: datetime,
        timezone: ZoneInfo,
    ) -> ScheduleTaskResponse:
        earliest, deadline = self._schedule_bounds(
            action.deadline,
            now,
            timezone,
            action.window_start,
            action.window_end,
        )
        if action.duration_minutes is None:
            raise InteractionError("Schedule action has no duration")
        return self.scheduler.schedule_task(
            task,
            ScheduleTaskRequest(
                duration_minutes=action.duration_minutes,
                earliest_iso=earliest,
                deadline_iso=deadline,
                create_event=action.create_event,
            ),
        )

    def _find_existing(self, title: str) -> VikunjaTask | None:
        query = self._normalize_title(title)
        candidates = [task for task in self.vikunja.list_tasks() if not task.done]
        exact = [task for task in candidates if self._normalize_title(task.title) == query]
        matches = exact or [
            task for task in candidates if query in self._normalize_title(task.title)
        ]
        if len(matches) > 1:
            choices = ", ".join(f"{task.id}: {task.title}" for task in matches[:5])
            raise AmbiguousTaskError(
                f'Multiple Vikunja tasks matched "{title}": {choices}'
            )
        return matches[0] if matches else None

    @staticmethod
    def _brief_audit(brief: DailyBriefResponse) -> InteractionAction:
        return InteractionAction(
            action="brief_generated",
            status="READ_ONLY",
            target=str(brief.date),
            details={
                "events": brief.summary.event_count,
                "work_blocks": brief.summary.work_block_count,
                "overdue_tasks": brief.summary.overdue_task_count,
                "conflicts": brief.summary.conflict_count,
            },
        )

    @staticmethod
    def _schedule_audit(
        task: VikunjaTask, scheduled: ScheduleTaskResponse
    ) -> InteractionAction:
        option = scheduled.selected_option
        return InteractionAction(
            action="task_scheduled",
            status=scheduled.status.value,
            target=f"vikunja-task:{task.id}",
            details={
                "start_iso": option.start_iso.isoformat(),
                "end_iso": option.end_iso.isoformat(),
            },
        )

    @staticmethod
    def _result(brief, scheduled, task, timezone: ZoneInfo, context_result=None) -> str:
        if isinstance(context_result, EntityContextResult):
            if context_result.status is ResolutionStatus.NOT_FOUND:
                return "Beacon has no context for that entity."
            if context_result.status is ResolutionStatus.AMBIGUOUS:
                names = ", ".join(
                    f"{item.canonical_name} ({item.type.value})"
                    for item in context_result.candidates
                )
                return f"That reference is ambiguous: {names}."
            entity = context_result.entity
            assert entity is not None
            details: list[str] = []
            if context_result.aliases:
                details.append("aliases: " + ", ".join(item.alias for item in context_result.aliases))
            details.extend(f"{item.predicate}: {item.value}" for item in context_result.facts)
            details.extend(
                f"{item.relationship}: {item.target.canonical_name}"
                for item in context_result.outgoing_relationships
            )
            details.extend(
                f"{item.source.canonical_name} {item.relationship} this {entity.type.value}"
                for item in context_result.incoming_relationships
            )
            suffix = "; ".join(details) if details else "no active details"
            return f"{entity.canonical_name} ({entity.type.value}) — {suffix}."
        if isinstance(context_result, ContextMutationResult):
            if context_result.status is MutationStatus.CONFLICT:
                return context_result.message
            subject = (
                f" for {context_result.entity.canonical_name}"
                if context_result.entity
                else ""
            )
            return f"{context_result.message.rstrip('.')}{subject}."
        if brief is not None:
            return brief.spoken_summary
        if scheduled is not None and task is not None:
            option = scheduled.selected_option
            verb = {
                "NEW": "Scheduled",
                "UPDATED": "Rescheduled",
                "UNCHANGED": "Already scheduled",
                "RECOMMENDATION_ONLY": "Recommended",
            }[scheduled.status.value]
            return (
                f'{verb} "{task.title}" from '
                f"{option.start_iso.astimezone(timezone).isoformat()} to "
                f"{option.end_iso.astimezone(timezone).isoformat()}."
            )
        if task is not None:
            return f'Created task "{task.title}".'
        raise InteractionError("Action plan produced no result")

    @staticmethod
    def _schedule_bounds(
        target_date: date | None,
        now: datetime,
        timezone: ZoneInfo,
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> tuple[datetime, datetime | None]:
        if target_date is None:
            return now, None
        start = time.fromisoformat(window_start or "09:00")
        end = time.fromisoformat(window_end or "22:00")
        earliest = datetime.combine(target_date, start, tzinfo=timezone)
        deadline = datetime.combine(target_date, end, tzinfo=timezone)
        if target_date == now.date():
            earliest = max(earliest, now)
        return earliest, deadline

    @staticmethod
    def _due_datetime(deadline: date | None, timezone: ZoneInfo) -> datetime | None:
        if deadline is None:
            return None
        return datetime.combine(deadline, time(22, 0), tzinfo=timezone)

    @staticmethod
    def _normalize_title(value: str) -> str:
        return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())
