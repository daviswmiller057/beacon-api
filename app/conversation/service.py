from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.config import Settings
from app.conversation.models import (
    ConversationError,
    ConversationProviderMetadata,
    ConversationRequest,
    ConversationResponse,
    ConversationStatus,
    ModelToolCall,
    ModelTurn,
    ModelUsage,
    StoredTurn,
)
from app.conversation.prompts import build_system_instruction
from app.conversation.provider import (
    ConversationModelProvider,
    ConversationProviderError,
)
from app.conversation.repository import ConversationRepository
from app.conversation.tools import BeaconToolRegistry
from app.models import InteractResponse
from app.services.interaction import InteractionService


logger = logging.getLogger("beacon.conversation")
_ACTION_SUCCESS = re.compile(
    r"\b(done|created|scheduled|saved|added|completed)\b", re.IGNORECASE
)


class ConversationDisabledError(RuntimeError):
    pass


class ConversationInputTooLong(RuntimeError):
    pass


class ConversationService:
    """Orchestrate one controlled model → Beacon → same-model turn."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: ConversationRepository,
        provider: ConversationModelProvider,
        interaction: InteractionService,
        tools: BeaconToolRegistry | None = None,
        clock: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.provider = provider
        self.interaction = interaction
        self.tools = tools or BeaconToolRegistry()
        self.clock = clock or (lambda timezone: datetime.now(timezone))

    async def converse(self, request: ConversationRequest) -> ConversationResponse:
        if not self.settings.conversation_enabled:
            raise ConversationDisabledError("conversation_feature_disabled")
        if len(request.message) > self.settings.conversation_max_input_length:
            raise ConversationInputTooLong("conversation_input_too_long")

        correlation_id = str(uuid.uuid4())
        turn = self.repository.begin_turn(
            session_id=request.session_id,
            client_message_id=request.client_message_id,
            message=request.message,
            correlation_id=correlation_id,
            provider=self.provider.name,
            model=self.provider.model,
        )
        if turn.cached_response is not None:
            return turn.cached_response

        timezone = ZoneInfo(self.settings.beacon_timezone)
        current = self.clock(timezone).astimezone(timezone)
        messages = self.repository.history(
            turn.session_id, self.settings.conversation_max_history_messages
        )
        declarations = self.tools.declarations()
        instruction = build_system_instruction(
            current_datetime=current,
            timezone_name=self.settings.beacon_timezone,
            capabilities=self.tools.capabilities(),
        )
        started = time.monotonic()
        try:
            initial = await self._begin_with_retry(
                messages=messages,
                system_instruction=instruction,
                tools=declarations,
            )
        except ConversationProviderError as exc:
            response = self._error_response(
                turn,
                status=ConversationStatus.PROVIDER_UNAVAILABLE,
                code=exc.code,
                stage="provider_initial",
                message="The conversation model is temporarily unavailable.",
                degraded=True,
            )
            self.repository.complete(turn=turn, response=response)
            self._log(turn, started, response, None)
            return response

        if not initial.tool_calls:
            reply = self._validate_direct_reply(initial.text)
            if reply is None:
                response = self._error_response(
                    turn,
                    status=ConversationStatus.FAILED,
                    code="unsafe_direct_response",
                    stage="direct_response",
                    message="Beacon could not produce a safe response.",
                    degraded=True,
                    interaction_id=initial.interaction_id,
                    usage=initial.usage,
                )
            else:
                response = self._response(
                    turn,
                    status=ConversationStatus.COMPLETED,
                    reply=reply,
                    interaction_id=initial.interaction_id,
                    usage=initial.usage,
                )
            self.repository.complete(turn=turn, response=response)
            self._log(turn, started, response, None)
            return response

        if len(initial.tool_calls) != 1:
            response = self._error_response(
                turn,
                status=ConversationStatus.SAFETY_REJECTED,
                code="multiple_tool_calls_rejected",
                stage="tool_validation",
                message="Submit one Beacon action at a time.",
                interaction_id=initial.interaction_id,
                usage=initial.usage,
            )
            self.repository.complete(turn=turn, response=response)
            self._log(turn, started, response, "multiple")
            return response

        response = await self._handle_tool_call(
            turn=turn,
            model_turn=initial,
            call=initial.tool_calls[0],
            messages=messages,
            instruction=instruction,
            declarations=declarations,
            current=current,
            allow_repair=self.settings.conversation_max_malformed_repairs > 0,
        )
        self.repository.complete(turn=turn, response=response)
        self._log(turn, started, response, initial.tool_calls[0].name)
        return response

    async def _handle_tool_call(
        self,
        *,
        turn: StoredTurn,
        model_turn: ModelTurn,
        call: ModelToolCall,
        messages,
        instruction: str,
        declarations,
        current: datetime,
        allow_repair: bool,
    ) -> ConversationResponse:
        registered = self.tools.get(call.name)
        if registered is None:
            return self._error_response(
                turn,
                status=ConversationStatus.UNSUPPORTED_TOOL,
                code="unknown_beacon_tool",
                stage="tool_validation",
                message="That operation is not available in Beacon.",
                interaction_id=model_turn.interaction_id,
                usage=model_turn.usage,
            )
        try:
            intent, validated_arguments = self.tools.validate(call.name, call.arguments)
        except ValidationError as exc:
            if allow_repair and self.settings.conversation_max_tool_rounds >= 2:
                rejection = {
                    "status": "invalid_arguments",
                    "errors": [
                        {"location": list(error["loc"]), "type": error["type"]}
                        for error in exc.errors(include_input=False, include_url=False)
                    ],
                }
                try:
                    repaired = await self.provider.continue_with_result(
                        tool_call=call,
                        result=rejection,
                        interaction_id=model_turn.interaction_id,
                        system_instruction=instruction,
                        tools=declarations,
                        messages=messages,
                    )
                except ConversationProviderError:
                    repaired = ModelTurn()
                if len(repaired.tool_calls) == 1:
                    return await self._handle_repaired_call(
                        turn=turn,
                        model_turn=repaired,
                        call=repaired.tool_calls[0],
                        current=current,
                    )
            return self._error_response(
                turn,
                status=ConversationStatus.INVALID_TOOL_CALL,
                code="invalid_tool_arguments",
                stage="tool_validation",
                message="The requested Beacon action was invalid.",
                interaction_id=model_turn.interaction_id,
                usage=model_turn.usage,
            )
        return await self._execute_and_render(
            turn=turn,
            model_turn=model_turn,
            call=call,
            validated_arguments=validated_arguments,
            intent=intent,
            messages=messages,
            instruction=instruction,
            declarations=declarations,
            current=current,
        )

    async def _handle_repaired_call(
        self,
        *,
        turn: StoredTurn,
        model_turn: ModelTurn,
        call: ModelToolCall,
        current: datetime,
    ) -> ConversationResponse:
        registered = self.tools.get(call.name)
        if registered is None:
            return self._error_response(
                turn,
                status=ConversationStatus.UNSUPPORTED_TOOL,
                code="unknown_beacon_tool",
                stage="tool_repair",
                message="That operation is not available in Beacon.",
                interaction_id=model_turn.interaction_id,
                usage=model_turn.usage,
            )
        try:
            intent, validated = self.tools.validate(call.name, call.arguments)
        except ValidationError:
            return self._error_response(
                turn,
                status=ConversationStatus.INVALID_TOOL_CALL,
                code="invalid_tool_arguments_after_repair",
                stage="tool_repair",
                message="The requested Beacon action remained invalid.",
                interaction_id=model_turn.interaction_id,
                usage=model_turn.usage,
            )
        self.repository.record_tool_call(
            turn=turn,
            tool_name=call.name,
            tool_call_id=call.id,
            arguments=validated,
            provider_interaction_id=model_turn.interaction_id,
        )
        result = await self._execute(intent, current)
        self.repository.record_tool_result(
            turn=turn,
            tool_name=call.name,
            tool_call_id=call.id,
            result=result,
        )
        status = self._conversation_status(result)
        return self._response(
            turn,
            status=status,
            reply=self._fallback(result, rendering_failed=True),
            beacon_result=result,
            degraded=True,
            interaction_id=model_turn.interaction_id,
            usage=model_turn.usage,
        )

    async def _execute_and_render(
        self,
        *,
        turn: StoredTurn,
        model_turn: ModelTurn,
        call: ModelToolCall,
        validated_arguments: dict[str, Any],
        intent,
        messages,
        instruction: str,
        declarations,
        current: datetime,
    ) -> ConversationResponse:
        self.repository.record_tool_call(
            turn=turn,
            tool_name=call.name,
            tool_call_id=call.id,
            arguments=validated_arguments,
            provider_interaction_id=model_turn.interaction_id,
        )
        result = await self._execute(intent, current)
        self.repository.record_tool_result(
            turn=turn,
            tool_name=call.name,
            tool_call_id=call.id,
            result=result,
        )
        status = self._conversation_status(result)
        try:
            final = await self.provider.continue_with_result(
                tool_call=call,
                result=result,
                interaction_id=model_turn.interaction_id,
                system_instruction=instruction,
                tools=declarations,
                messages=messages,
            )
            reply = self._validated_render(final, status)
            if reply is None:
                raise ConversationProviderError(
                    "unsafe final response", code="unsafe_final_response"
                )
        except ConversationProviderError:
            return self._response(
                turn,
                status=status,
                reply=self._fallback(result, rendering_failed=True),
                beacon_result=result,
                degraded=True,
                interaction_id=model_turn.interaction_id,
                usage=model_turn.usage,
                error=ConversationError(
                    code="final_rendering_failed",
                    stage="provider_rendering",
                    message=(
                        "The action result is authoritative; natural-language "
                        "rendering failed."
                    ),
                ),
            )
        return self._response(
            turn,
            status=status,
            reply=reply,
            beacon_result=result,
            interaction_id=final.interaction_id or model_turn.interaction_id,
            usage=self._combine_usage(model_turn.usage, final.usage),
        )

    async def _execute(self, intent, current: datetime) -> dict[str, Any]:
        try:
            # Beacon's deterministic core is currently synchronous. Existing service
            # clients own their timeout behavior; the model SDK itself remains async.
            response = self.interaction.execute_structured_intent(intent, now=current)
        except Exception as exc:
            return {
                "status": "failed",
                "error_code": self._error_code(exc),
                "error": self._safe_error_detail(exc),
            }
        return self._authoritative_result(response)

    async def _begin_with_retry(self, **kwargs) -> ModelTurn:
        attempts = self.settings.conversation_provider_max_retries + 1
        for attempt in range(attempts):
            try:
                return await self.provider.begin(**kwargs)
            except ConversationProviderError as exc:
                if not exc.retryable or attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(0.1 * (attempt + 1))
        raise AssertionError("provider retry loop exhausted")

    def _validate_direct_reply(self, text: str | None) -> str | None:
        if not text:
            return None
        clean = text.strip()
        if not clean or len(clean) > self.settings.conversation_max_output_length:
            return None
        if _ACTION_SUCCESS.search(clean):
            return None
        return clean

    def _validated_render(
        self, turn: ModelTurn, status: ConversationStatus
    ) -> str | None:
        if turn.tool_calls or not turn.text:
            return None
        clean = turn.text.strip()
        if not clean or len(clean) > self.settings.conversation_max_output_length:
            return None
        if status is not ConversationStatus.COMPLETED and _ACTION_SUCCESS.search(clean):
            return None
        return clean

    @staticmethod
    def _authoritative_result(response: InteractResponse) -> dict[str, Any]:
        status = "completed"
        created_count = 0
        failed_count = 0
        if response.calendar_batch is not None:
            created_count = response.calendar_batch.completed_count
            failed_count = response.calendar_batch.failed_count
            status = response.calendar_batch.status.value.casefold()
        elif any(action.status == "PENDING" for action in response.actions_taken):
            status = "clarification_required"
        elif any(action.status == "FAILED" for action in response.actions_taken):
            status = "failed"
        return {
            "status": status,
            "created_count": created_count,
            "failed_count": failed_count,
            "result": response.model_dump(mode="json"),
        }

    @staticmethod
    def _conversation_status(result: dict[str, Any]) -> ConversationStatus:
        value = result.get("status")
        if value in {"completed", "complete"}:
            return ConversationStatus.COMPLETED
        if value == "clarification_required":
            return ConversationStatus.CLARIFICATION_REQUIRED
        if value == "partial":
            return ConversationStatus.PARTIAL
        return ConversationStatus.FAILED

    @staticmethod
    def _fallback(result: dict[str, Any], *, rendering_failed: bool) -> str:
        suffix = " Natural-language rendering unavailable." if rendering_failed else ""
        status = result.get("status")
        if status == "complete":
            return f"Created {result.get('created_count', 0)} calendar events.{suffix}"
        if status == "partial":
            return (
                f"Partially completed: {result.get('created_count', 0)} succeeded and "
                f"{result.get('failed_count', 0)} failed.{suffix}"
            )
        if status == "clarification_required":
            nested = result.get("result") or {}
            return str(nested.get("result") or "More information is required.") + suffix
        return "Beacon did not complete the requested action." + suffix

    def _response(
        self,
        turn: StoredTurn,
        *,
        status: ConversationStatus,
        reply: str,
        beacon_result: dict[str, Any] | None = None,
        degraded: bool = False,
        interaction_id: str | None = None,
        usage: ModelUsage | None = None,
        error: ConversationError | None = None,
    ) -> ConversationResponse:
        return ConversationResponse(
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            status=status,
            reply=reply,
            beacon_result=beacon_result,
            degraded=degraded,
            provider=ConversationProviderMetadata(
                provider=self.provider.name,
                model=self.provider.model,
                interaction_id=interaction_id,
                usage=usage,
            ),
            correlation_id=turn.correlation_id,
            error=error,
        )

    def _error_response(
        self,
        turn: StoredTurn,
        *,
        status: ConversationStatus,
        code: str,
        stage: str,
        message: str,
        degraded: bool = False,
        interaction_id: str | None = None,
        usage: ModelUsage | None = None,
    ) -> ConversationResponse:
        return self._response(
            turn,
            status=status,
            reply=message,
            degraded=degraded,
            interaction_id=interaction_id,
            usage=usage,
            error=ConversationError(code=code, stage=stage, message=message),
        )

    @staticmethod
    def _combine_usage(
        first: ModelUsage | None, second: ModelUsage | None
    ) -> ModelUsage | None:
        if first is None:
            return second
        if second is None:
            return first

        def total(left: int | None, right: int | None) -> int | None:
            return None if left is None and right is None else (left or 0) + (right or 0)

        return ModelUsage(
            input_tokens=total(first.input_tokens, second.input_tokens),
            output_tokens=total(first.output_tokens, second.output_tokens),
            total_tokens=total(first.total_tokens, second.total_tokens),
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        value = re.sub(r"(?<!^)(?=[A-Z])", "_", error.__class__.__name__).casefold()
        return value[:100]

    @staticmethod
    def _safe_error_detail(error: Exception) -> str:
        detail = str(error)
        allowlisted_prefixes = (
            "daily_range_",
            "SCHEDULE_TASK requires",
            "CREATE_TASK requires",
            "CREATE_CALENDAR_EVENTS requires",
        )
        if detail.startswith(allowlisted_prefixes):
            return detail[:500]
        return "Beacon execution failed before a complete result was available."

    @staticmethod
    def _log(
        turn: StoredTurn,
        started: float,
        response: ConversationResponse,
        tool_name: str | None,
    ) -> None:
        logger.info(
            "conversation_turn session=%s turn=%s correlation=%s provider=%s "
            "model=%s latency_ms=%d tool=%s status=%s degraded=%s",
            turn.session_id,
            turn.turn_id,
            turn.correlation_id,
            response.provider.provider,
            response.provider.model,
            int((time.monotonic() - started) * 1000),
            tool_name or "none",
            response.status.value,
            response.degraded,
        )
