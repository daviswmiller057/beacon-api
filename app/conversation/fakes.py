from __future__ import annotations

from collections import deque
from typing import Any

from app.conversation.models import (
    ModelMessage,
    ModelToolCall,
    ModelTurn,
    ToolDeclaration,
)


class ScriptedConversationProvider:
    """Deterministic provider fake for model, timeout, and rendering tests."""

    name = "fake"

    def __init__(self, responses: list[ModelTurn | Exception], model: str = "fake-1"):
        self.model = model
        self.responses = deque(responses)
        self.begin_calls: list[dict[str, Any]] = []
        self.continuation_calls: list[dict[str, Any]] = []

    async def begin(
        self,
        *,
        messages: list[ModelMessage],
        system_instruction: str,
        tools: list[ToolDeclaration],
    ) -> ModelTurn:
        self.begin_calls.append(
            {
                "messages": messages,
                "system_instruction": system_instruction,
                "tools": tools,
            }
        )
        return self._next()

    async def continue_with_result(
        self,
        *,
        tool_call: ModelToolCall,
        result: dict,
        interaction_id: str | None,
        system_instruction: str,
        tools: list[ToolDeclaration],
        messages: list[ModelMessage],
    ) -> ModelTurn:
        self.continuation_calls.append(
            {
                "tool_call": tool_call,
                "result": result,
                "interaction_id": interaction_id,
                "system_instruction": system_instruction,
                "tools": tools,
                "messages": messages,
            }
        )
        return self._next()

    def _next(self) -> ModelTurn:
        if not self.responses:
            raise AssertionError("Scripted conversation provider exhausted")
        value = self.responses.popleft()
        if isinstance(value, Exception):
            raise value
        return value
