import asyncio
from types import SimpleNamespace

import httpx

from app.api import conversation as conversation_api
from app.api.conversation import conversation_service_dependency
from app.conversation.models import (
    ConversationProviderMetadata,
    ConversationResponse,
    ConversationStatus,
)
from app.main import app
from app.security import require_api_key


class FakeConversationService:
    def __init__(self):
        self.requests = []

    async def converse(self, request):
        self.requests.append(request)
        return ConversationResponse(
            session_id=request.session_id or "session-1",
            turn_id="turn-1",
            status=ConversationStatus.COMPLETED,
            reply="Synthetic reply",
            beacon_result={"status": "completed"},
            provider=ConversationProviderMetadata(provider="fake", model="fake-1"),
            correlation_id="correlation-1",
        )


def test_conversation_service_factory_returns_constructed_service(monkeypatch):
    expected = object()
    settings = SimpleNamespace(
        conversation_enabled=True,
        conversation_provider="gemini",
        gemini_api_key="synthetic-key",
        context_database_path="synthetic.db",
        conversation_model="gemini-test",
        conversation_provider_timeout_seconds=1.0,
        conversation_max_output_tokens=128,
    )

    class FakeDatabase:
        def __init__(self, path):
            assert path == "synthetic.db"

        def upgrade(self):
            return None

    monkeypatch.setattr(conversation_api, "get_settings", lambda: settings)
    monkeypatch.setattr(conversation_api, "ContextDatabase", FakeDatabase)
    monkeypatch.setattr(conversation_api, "GeminiConversationProvider", lambda **_: object())
    monkeypatch.setattr(conversation_api, "ConversationRepository", lambda _: object())
    monkeypatch.setattr(conversation_api, "InteractionService", lambda **_: object())
    monkeypatch.setattr(conversation_api, "ConversationService", lambda **_: expected)
    conversation_api.get_conversation_service.cache_clear()
    try:
        assert conversation_api.get_conversation_service() is expected
    finally:
        conversation_api.get_conversation_service.cache_clear()


def test_conversation_endpoint_returns_text_and_authoritative_result():
    service = FakeConversationService()
    async def service_dependency():
        return service

    async def authentication_dependency():
        return None

    app.dependency_overrides[conversation_service_dependency] = service_dependency
    app.dependency_overrides[require_api_key] = authentication_dependency
    try:
        async def call():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://beacon.test"
            ) as client:
                return await client.post(
                    "/v1/conversation",
                    json={
                        "message": "Synthetic request",
                        "client_message_id": "client-1",
                    },
                )

        response = asyncio.run(call())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "status": "completed",
        "reply": "Synthetic reply",
        "beacon_result": {"status": "completed"},
        "degraded": False,
        "provider": {
            "provider": "fake",
            "model": "fake-1",
            "interaction_id": None,
            "usage": None,
        },
        "correlation_id": "correlation-1",
        "error": None,
        "idempotent_replay": False,
    }
    assert service.requests[0].client_message_id == "client-1"


def test_conversation_request_rejects_unknown_fields_and_missing_idempotency_key():
    service = FakeConversationService()
    async def service_dependency():
        return service

    async def authentication_dependency():
        return None

    app.dependency_overrides[conversation_service_dependency] = service_dependency
    app.dependency_overrides[require_api_key] = authentication_dependency
    try:
        async def call():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://beacon.test"
            ) as client:
                unknown = await client.post(
                    "/v1/conversation",
                    json={
                        "message": "Hi",
                        "client_message_id": "one",
                        "api_key": "leak",
                    },
                )
                missing = await client.post(
                    "/v1/conversation", json={"message": "Hi"}
                )
                return unknown, missing

        unknown, missing = asyncio.run(call())
    finally:
        app.dependency_overrides.clear()
    assert unknown.status_code == 422
    assert missing.status_code == 422
    assert service.requests == []
