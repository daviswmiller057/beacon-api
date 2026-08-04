from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.context.database import ContextDatabase
from app.conversation.models import (
    ConversationResponse,
    ModelMessage,
    ModelRole,
    StoredTurn,
)


class ConversationPersistenceError(RuntimeError):
    """Stable base error for local conversation persistence failures."""


class IdempotencyConflict(ConversationPersistenceError):
    pass


class ConcurrentTurnConflict(ConversationPersistenceError):
    pass


class SessionNotFound(ConversationPersistenceError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _input_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ConversationRepository:
    """SQLite conversation state; each method uses its own safe transaction."""

    def __init__(self, database: ContextDatabase) -> None:
        self.database = database

    def begin_turn(
        self,
        *,
        session_id: str | None,
        client_message_id: str,
        message: str,
        correlation_id: str,
        provider: str,
        model: str,
    ) -> StoredTurn:
        normalized = _normalized_text(message)
        digest = _input_hash(normalized)
        requested_session = session_id
        session_id = session_id or str(uuid.uuid4())
        turn_id = str(uuid.uuid4())
        timestamp = _now()
        with self.database.transaction() as connection:
            session = connection.execute(
                "SELECT sequence, active_turn_id FROM conversation_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                if requested_session is not None:
                    raise SessionNotFound("conversation_session_not_found")
                connection.execute(
                    "INSERT INTO conversation_sessions"
                    "(id, created_at, updated_at, status, provider, model, sequence) "
                    "VALUES (?, ?, ?, 'active', ?, ?, 0)",
                    (session_id, timestamp, timestamp, provider, model),
                )
                sequence = 1
            else:
                existing = connection.execute(
                    "SELECT input_hash, response_json FROM conversation_turns "
                    "WHERE session_id = ? AND client_message_id = ?",
                    (session_id, client_message_id),
                ).fetchone()
                if existing is not None:
                    if existing["input_hash"] != digest:
                        raise IdempotencyConflict("client_message_id_content_conflict")
                    if existing["response_json"] is None:
                        raise ConcurrentTurnConflict("conversation_turn_in_progress")
                    cached = ConversationResponse.model_validate_json(
                        existing["response_json"]
                    ).model_copy(update={"idempotent_replay": True})
                    return StoredTurn(
                        session_id=session_id,
                        turn_id=cached.turn_id,
                        sequence=0,
                        correlation_id=cached.correlation_id,
                        cached_response=cached,
                    )
                if session["active_turn_id"] is not None:
                    raise ConcurrentTurnConflict("conversation_turn_in_progress")
                sequence = int(session["sequence"]) + 1

            connection.execute(
                "INSERT INTO conversation_turns"
                "(id, session_id, sequence, client_message_id, input_hash, "
                "user_text, normalized_text, status, correlation_id, provider, model, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'processing', "
                "?, ?, ?, ?, ?)",
                (
                    turn_id,
                    session_id,
                    sequence,
                    client_message_id,
                    digest,
                    message,
                    normalized,
                    correlation_id,
                    provider,
                    model,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE conversation_sessions SET sequence = ?, active_turn_id = ?, "
                "updated_at = ? WHERE id = ?",
                (sequence, turn_id, timestamp, session_id),
            )
            message_sequence = self._next_message_sequence(connection, session_id)
            connection.execute(
                "INSERT INTO conversation_messages"
                "(id, session_id, turn_id, sequence, role, text, created_at) "
                "VALUES (?, ?, ?, ?, 'user', ?, ?)",
                (
                    str(uuid.uuid4()),
                    session_id,
                    turn_id,
                    message_sequence,
                    normalized,
                    timestamp,
                ),
            )
        return StoredTurn(
            session_id=session_id,
            turn_id=turn_id,
            sequence=sequence,
            correlation_id=correlation_id,
        )

    def history(self, session_id: str, limit: int) -> list[ModelMessage]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT role, text, tool_name, tool_call_id, content_json FROM "
                "conversation_messages WHERE session_id = ? ORDER BY sequence DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        finally:
            connection.close()
        return [
            ModelMessage(
                role=ModelRole(row["role"]),
                text=row["text"],
                tool_name=row["tool_name"],
                tool_call_id=row["tool_call_id"],
                content=(json.loads(row["content_json"]) if row["content_json"] else None),
            )
            for row in reversed(rows)
        ]

    def record_tool_call(
        self,
        *,
        turn: StoredTurn,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        provider_interaction_id: str | None,
    ) -> None:
        encoded = json.dumps(arguments, separators=(",", ":"), sort_keys=True)
        timestamp = _now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE conversation_turns SET tool_name = ?, tool_call_id = ?, "
                "tool_arguments_json = ?, provider_interaction_id = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    tool_name,
                    tool_call_id,
                    encoded,
                    provider_interaction_id,
                    timestamp,
                    turn.turn_id,
                ),
            )
            self._insert_message(
                connection,
                turn,
                ModelRole.TOOL_CALL,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content=arguments,
                timestamp=timestamp,
            )

    def record_tool_result(
        self, *, turn: StoredTurn, tool_name: str, tool_call_id: str, result: dict[str, Any]
    ) -> None:
        encoded = json.dumps(result, default=str, separators=(",", ":"), sort_keys=True)
        timestamp = _now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE conversation_turns SET beacon_result_json = ?, updated_at = ? "
                "WHERE id = ?",
                (encoded, timestamp, turn.turn_id),
            )
            self._insert_message(
                connection,
                turn,
                ModelRole.TOOL_RESULT,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content=result,
                timestamp=timestamp,
            )

    def complete(
        self,
        *,
        turn: StoredTurn,
        response: ConversationResponse,
    ) -> None:
        response_json = response.model_dump_json()
        provider_interaction_id = response.provider.interaction_id
        usage_json = (
            response.provider.usage.model_dump_json()
            if response.provider.usage is not None
            else None
        )
        timestamp = _now()
        with self.database.transaction() as connection:
            updated = connection.execute(
                "UPDATE conversation_turns SET status = ?, assistant_response = ?, "
                "response_json = ?, degraded = ?, error_code = ?, usage_json = ?, "
                "provider_interaction_id = COALESCE(?, provider_interaction_id), "
                "updated_at = ? WHERE id = ? AND status = 'processing'",
                (
                    response.status.value,
                    response.reply,
                    response_json,
                    int(response.degraded),
                    response.error.code if response.error else None,
                    usage_json,
                    provider_interaction_id,
                    timestamp,
                    turn.turn_id,
                ),
            )
            if updated.rowcount != 1:
                raise ConversationPersistenceError("conversation_turn_not_processing")
            self._insert_message(
                connection,
                turn,
                ModelRole.ASSISTANT,
                text=response.reply,
                timestamp=timestamp,
            )
            connection.execute(
                "UPDATE conversation_sessions SET active_turn_id = NULL, "
                "provider_interaction_id = COALESCE(?, provider_interaction_id), "
                "updated_at = ? WHERE id = ? AND active_turn_id = ?",
                (provider_interaction_id, timestamp, turn.session_id, turn.turn_id),
            )

    def count_messages(self, session_id: str) -> int:
        connection = self.database.connect()
        try:
            return int(
                connection.execute(
                    "SELECT count(*) FROM conversation_messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
            )
        finally:
            connection.close()

    @staticmethod
    def _next_message_sequence(connection, session_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(max(sequence), 0) + 1 FROM conversation_messages "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0])

    def _insert_message(
        self,
        connection,
        turn: StoredTurn,
        role: ModelRole,
        *,
        text: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        content: dict[str, Any] | None = None,
        timestamp: str,
    ) -> None:
        connection.execute(
            "INSERT INTO conversation_messages"
            "(id, session_id, turn_id, sequence, role, text, tool_name, "
            "tool_call_id, content_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                turn.session_id,
                turn.turn_id,
                self._next_message_sequence(connection, turn.session_id),
                role.value,
                text,
                tool_name,
                tool_call_id,
                json.dumps(content, default=str, separators=(",", ":"), sort_keys=True)
                if content is not None
                else None,
                timestamp,
            ),
        )
