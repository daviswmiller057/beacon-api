from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.context.domain import Provenance
from app.context.service import ContextRegistryService
from app.intake.errors import (
    AmbiguousTaskError,
    InteractionError,
    InteractionTaskNotFound,
    UnsupportedIntentError,
)
from app.intake.executor import ActionExecutor
from app.intake.gemini import GeminiInterpreter
from app.intake.interpreter import IntentInterpreter
from app.intake.planner import ActionPlanner
from app.intake.rules import RuleBasedIntentInterpreter
from app.models import InteractRequest, InteractResponse, StructuredIntent
from app.services.daily_brief import DailyBriefService
from app.services.scheduler import SchedulerService
from app.services.vikunja_client import VikunjaClient


class InteractionService:
    def __init__(
        self,
        *,
        vikunja: VikunjaClient | None = None,
        scheduler: SchedulerService | None = None,
        daily_brief: DailyBriefService | None = None,
        interpreter: IntentInterpreter | None = None,
        planner: ActionPlanner | None = None,
        executor: ActionExecutor | None = None,
        settings: Settings | None = None,
        clock: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vikunja = vikunja or VikunjaClient(self.settings)
        self.scheduler = scheduler or SchedulerService()
        self.daily_brief = daily_brief or DailyBriefService()
        self.clock = clock or (lambda timezone: datetime.now(timezone))
        self.interpreter = interpreter
        self.planner = planner or ActionPlanner(self.settings)
        self.executor = executor or ActionExecutor(
            vikunja=self.vikunja,
            scheduler=self.scheduler,
            daily_brief=self.daily_brief,
        )

    def interact(self, request: InteractRequest) -> InteractResponse:
        timezone = ZoneInfo(self.settings.beacon_timezone)
        now = self.clock(timezone).astimezone(timezone)
        if request.intent is not None:
            intent = request.intent
        else:
            interpreter = self.interpreter or self._build_interpreter()
            intent = interpreter.interpret(request.message or "", now.date())
        if request.message is not None and intent.intent.value == "STORE_CONTEXT":
            intent = intent.model_copy(
                update={"provenance": Provenance.EXPLICIT_USER_STATEMENT}
            )
        return self.execute_structured_intent(intent, now=now)

    def execute_structured_intent(
        self,
        intent: StructuredIntent,
        *,
        now: datetime | None = None,
    ) -> InteractResponse:
        """Run one validated intent through Beacon's shared deterministic core."""
        timezone = ZoneInfo(self.settings.beacon_timezone)
        current = (now or self.clock(timezone)).astimezone(timezone)
        plan = self.planner.plan(intent, current.date())
        if intent.intent.value.endswith("_CONTEXT") and self.executor.context_registry is None:
            self.executor.context_registry = ContextRegistryService.from_path(
                self.settings.context_database_path
            )
        return self.executor.execute(plan, current, timezone)

    def _build_interpreter(self) -> IntentInterpreter:
        provider = self.settings.beacon_interpreter
        if provider == "rules":
            return RuleBasedIntentInterpreter(self.settings)
        if provider == "gemini":
            return GeminiInterpreter(
                api_key=self.settings.gemini_api_key,
                model=self.settings.gemini_model,
                base_url=self.settings.gemini_api_base_url,
            )
        raise ValueError(f"Unsupported BEACON_INTERPRETER: {provider}")
