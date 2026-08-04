import pytest

from app.context.database import ContextDatabase
from app.conversation.models import (
    ConversationProviderMetadata,
    ConversationResponse,
    ConversationStatus,
)
from app.conversation.repository import (
    ConcurrentTurnConflict,
    ConversationRepository,
    IdempotencyConflict,
)


def repository(tmp_path):
    database = ContextDatabase(str(tmp_path / "beacon.db"))
    database.upgrade()
    return ConversationRepository(database)


def response(turn):
    return ConversationResponse(
        session_id=turn.session_id,
        turn_id=turn.turn_id,
        status=ConversationStatus.COMPLETED,
        reply="Synthetic response",
        provider=ConversationProviderMetadata(provider="fake", model="fake-1"),
        correlation_id=turn.correlation_id,
    )


def test_session_history_and_idempotent_response_survive_repository_recreation(tmp_path):
    repo = repository(tmp_path)
    turn = repo.begin_turn(
        session_id=None,
        client_message_id="client-1",
        message="  Synthetic   message ",
        correlation_id="correlation-1",
        provider="fake",
        model="fake-1",
    )
    repo.complete(turn=turn, response=response(turn))

    recreated = ConversationRepository(ContextDatabase(str(tmp_path / "beacon.db")))
    history = recreated.history(turn.session_id, 20)
    replay = recreated.begin_turn(
        session_id=turn.session_id,
        client_message_id="client-1",
        message="Synthetic message",
        correlation_id="different",
        provider="fake",
        model="fake-1",
    )

    assert [message.text for message in history] == [
        "Synthetic message",
        "Synthetic response",
    ]
    assert replay.cached_response.idempotent_replay is True
    assert replay.cached_response.turn_id == turn.turn_id


def test_message_id_content_conflict_is_rejected(tmp_path):
    repo = repository(tmp_path)
    turn = repo.begin_turn(
        session_id=None,
        client_message_id="same-id",
        message="first",
        correlation_id="c1",
        provider="fake",
        model="fake-1",
    )
    repo.complete(turn=turn, response=response(turn))

    with pytest.raises(IdempotencyConflict, match="content_conflict"):
        repo.begin_turn(
            session_id=turn.session_id,
            client_message_id="same-id",
            message="different",
            correlation_id="c2",
            provider="fake",
            model="fake-1",
        )


def test_active_turn_serializes_one_session(tmp_path):
    repo = repository(tmp_path)
    first = repo.begin_turn(
        session_id=None,
        client_message_id="one",
        message="first",
        correlation_id="c1",
        provider="fake",
        model="fake-1",
    )
    with pytest.raises(ConcurrentTurnConflict, match="in_progress"):
        repo.begin_turn(
            session_id=first.session_id,
            client_message_id="two",
            message="second",
            correlation_id="c2",
            provider="fake",
            model="fake-1",
        )


def test_history_is_bounded_without_deleting_old_messages(tmp_path):
    repo = repository(tmp_path)
    session_id = None
    for index in range(4):
        turn = repo.begin_turn(
            session_id=session_id,
            client_message_id=f"m-{index}",
            message=f"user {index}",
            correlation_id=f"c-{index}",
            provider="fake",
            model="fake-1",
        )
        session_id = turn.session_id
        repo.complete(
            turn=turn,
            response=response(turn).model_copy(update={"reply": f"assistant {index}"}),
        )
    assert repo.count_messages(session_id) == 8
    assert [message.text for message in repo.history(session_id, 3)] == [
        "assistant 2",
        "user 3",
        "assistant 3",
    ]
