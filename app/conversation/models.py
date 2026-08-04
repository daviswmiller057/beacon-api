from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


class ModelMessage(BaseModel):
    role: ModelRole
    text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    content: dict[str, Any] | None = None


class ModelToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ModelUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ModelTurn(BaseModel):
    text: str | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    interaction_id: str | None = None
    usage: ModelUsage | None = None


class ToolDeclaration(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    read_only: bool = False


class ConversationStatus(StrEnum):
    COMPLETED = "completed"
    CLARIFICATION_REQUIRED = "clarification_required"
    PARTIAL = "partial"
    FAILED = "failed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_TOOL_CALL = "invalid_tool_call"
    UNSUPPORTED_TOOL = "unsupported_tool"
    SAFETY_REJECTED = "safety_rejected"


class ConversationError(BaseModel):
    code: str
    stage: str
    message: str


class ConversationProviderMetadata(BaseModel):
    provider: str
    model: str
    interaction_id: str | None = None
    usage: ModelUsage | None = None


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=16000)
    client_message_id: str = Field(min_length=1, max_length=200)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("message", "client_message_id")
    @classmethod
    def required_text_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value_must_not_be_blank")
        return value

    @field_validator("session_id")
    @classmethod
    def optional_text_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value_must_not_be_blank")
        return value


class ConversationResponse(BaseModel):
    session_id: str
    turn_id: str
    status: ConversationStatus
    reply: str
    beacon_result: dict[str, Any] | None = None
    degraded: bool = False
    provider: ConversationProviderMetadata
    correlation_id: str
    error: ConversationError | None = None
    idempotent_replay: bool = False


class StoredTurn(BaseModel):
    session_id: str
    turn_id: str
    sequence: int
    correlation_id: str
    cached_response: ConversationResponse | None = None


class ConversationSession(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    status: str
    provider: str
    model: str
    sequence: int
