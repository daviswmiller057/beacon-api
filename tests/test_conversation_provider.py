import asyncio

import pytest

from app.conversation.gemini import GeminiConversationProvider
from app.conversation.models import (
    ModelMessage,
    ModelRole,
    ModelToolCall,
    ToolDeclaration,
)
from app.conversation.provider import ConversationProviderError


class FakeInteractions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeClient:
    def __init__(self, responses):
        self.aio = type("AsyncClient", (), {})()
        self.aio.interactions = FakeInteractions(responses)


def provider(client):
    return GeminiConversationProvider(
        api_key="",
        model="gemini-test",
        timeout_seconds=1,
        max_output_tokens=128,
        client=client,
    )


def declaration():
    return ToolDeclaration(
        name="create_task",
        description="Create a task",
        parameters={"type": "object", "properties": {"title": {"type": "string"}}},
    )


def test_gemini_interactions_response_maps_to_provider_neutral_tool_call():
    client = FakeClient(
        [
            {
                "id": "interaction-1",
                "steps": [
                    {
                        "type": "function_call",
                        "id": "call-9",
                        "name": "create_task",
                        "arguments": {"title": "Synthetic task"},
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            }
        ]
    )
    result = asyncio.run(
        provider(client).begin(
            messages=[ModelMessage(role=ModelRole.USER, text="Create a task")],
            system_instruction="safe instructions",
            tools=[declaration()],
        )
    )

    assert result.interaction_id == "interaction-1"
    assert result.tool_calls == [
        ModelToolCall(
            id="call-9", name="create_task", arguments={"title": "Synthetic task"}
        )
    ]
    assert result.usage.total_tokens == 14
    request = client.aio.interactions.calls[0]
    assert request["tools"][0]["name"] == "create_task"
    assert request["store"] is True
    assert "safe instructions" == request["system_instruction"]


def test_function_result_continuation_preserves_call_association():
    client = FakeClient([{"id": "interaction-2", "output_text": "Done.", "steps": []}])
    call = ModelToolCall(id="call-12", name="create_task", arguments={"title": "X"})

    result = asyncio.run(
        provider(client).continue_with_result(
            tool_call=call,
            result={"status": "completed"},
            interaction_id="interaction-1",
            system_instruction="safe instructions",
            tools=[declaration()],
            messages=[],
        )
    )

    assert result.text == "Done."
    request = client.aio.interactions.calls[0]
    assert request["previous_interaction_id"] == "interaction-1"
    assert request["input"][0] == {
        "type": "function_result",
        "name": "create_task",
        "call_id": "call-12",
        "result": {"status": "completed"},
    }


class HttpFailure(RuntimeError):
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    [
        (429, "provider_rate_limited", True),
        (503, "provider_server_error", True),
        (400, "provider_request_failed", False),
    ],
)
def test_provider_errors_map_to_stable_application_errors(status_code, code, retryable):
    client = FakeClient([HttpFailure(status_code)])
    with pytest.raises(ConversationProviderError) as raised:
        asyncio.run(
            provider(client).begin(
                messages=[], system_instruction="safe", tools=[declaration()]
            )
        )
    assert raised.value.code == code
    assert raised.value.retryable is retryable


def test_google_sdk_imports_are_isolated_to_gemini_adapter():
    from pathlib import Path

    offenders = []
    for source in Path("app").rglob("*.py"):
        if source.as_posix() == "app/conversation/gemini.py":
            continue
        text = source.read_text()
        if "from google" in text or "import google" in text:
            offenders.append(source.as_posix())
    assert offenders == []
