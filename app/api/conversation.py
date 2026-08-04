from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import get_settings
from app.context.database import ContextDatabase
from app.conversation.gemini import GeminiConversationProvider
from app.conversation.models import ConversationRequest, ConversationResponse
from app.conversation.provider import ConversationProviderError
from app.conversation.repository import (
    ConcurrentTurnConflict,
    ConversationPersistenceError,
    ConversationRepository,
    IdempotencyConflict,
    SessionNotFound,
)
from app.conversation.service import (
    ConversationDisabledError,
    ConversationInputTooLong,
    ConversationService,
)
from app.security import require_api_key
from app.services.interaction import InteractionService


router = APIRouter(
    prefix="/v1/conversation",
    tags=["conversation"],
    dependencies=[Depends(require_api_key)],
)


@lru_cache
def get_conversation_service() -> ConversationService:
    settings = get_settings()
    if not settings.conversation_enabled:
        raise ConversationDisabledError("conversation_feature_disabled")
    if settings.conversation_provider != "gemini":
        raise ConversationProviderError(
            "Conversation provider is unsupported",
            code="provider_unsupported",
        )
    if not settings.gemini_api_key:
        raise ConversationProviderError(
            "GEMINI_API_KEY is required for text conversation",
            code="provider_not_configured",
        )
    database = ContextDatabase(settings.context_database_path)
    database.upgrade()
    provider = GeminiConversationProvider(
        api_key=settings.gemini_api_key,
        model=settings.conversation_model,
        timeout_seconds=settings.conversation_provider_timeout_seconds,
        max_output_tokens=settings.conversation_max_output_tokens,
    )
    return ConversationService(
        settings=settings,
        repository=ConversationRepository(database),
        provider=provider,
        interaction=InteractionService(settings=settings),
    )


async def conversation_service_dependency() -> ConversationService:
    try:
        return get_conversation_service()
    except ConversationDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": str(exc), "stage": "configuration"},
        ) from exc
    except ConversationProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "stage": "configuration"},
        ) from exc


@router.post("", response_model=ConversationResponse)
async def converse(
    request: ConversationRequest,
    service: ConversationService = Depends(conversation_service_dependency),
) -> ConversationResponse:
    try:
        return await service.converse(request)
    except ConversationInputTooLong as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": str(exc), "stage": "request_validation"},
        ) from exc
    except SessionNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": str(exc), "stage": "session"},
        ) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": str(exc), "stage": "idempotency"},
        ) from exc
    except ConcurrentTurnConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": str(exc), "stage": "concurrency"},
        ) from exc
    except ConversationPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "conversation_persistence_failed", "stage": "persistence"},
        ) from exc
