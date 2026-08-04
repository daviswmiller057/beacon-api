from typing import Protocol

from app.conversation.models import (
    ModelMessage,
    ModelToolCall,
    ModelTurn,
    ToolDeclaration,
)


class ConversationProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ConversationProviderTimeout(ConversationProviderError):
    def __init__(self, message: str = "Conversation provider timed out") -> None:
        super().__init__(message, code="provider_timeout", retryable=True)


class ConversationModelProvider(Protocol):
    name: str
    model: str

    async def begin(
        self,
        *,
        messages: list[ModelMessage],
        system_instruction: str,
        tools: list[ToolDeclaration],
    ) -> ModelTurn: ...

    async def continue_with_result(
        self,
        *,
        tool_call: ModelToolCall,
        result: dict,
        interaction_id: str | None,
        system_instruction: str,
        tools: list[ToolDeclaration],
        messages: list[ModelMessage],
    ) -> ModelTurn: ...
