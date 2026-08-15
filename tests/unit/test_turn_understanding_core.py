from pathlib import Path

from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.context import ConversationContextBuilder
from companion.contracts import ConversationMessage
from companion.core import CompanionCore
from companion.turn_understanding import SqliteTurnUnderstandingRepository


def test_core_enqueues_bounded_shadow_context_without_changing_response(
    tmp_path: Path,
) -> None:
    history = [
        ConversationMessage("user", "범위 밖의 오래된 말"),
        ConversationMessage("assistant", "범위 밖의 오래된 답"),
        ConversationMessage("user", "바로 전 말"),
        ConversationMessage("assistant", "바로 전 답"),
    ]
    shadow = SqliteTurnUnderstandingRepository(tmp_path / "turn.sqlite")
    with_shadow = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(_messages=history.copy()),
        ConversationContextBuilder(max_messages=2, max_characters=100),
        turn_understanding_repository=shadow,
        turn_source="cli",
    )
    without_shadow = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(_messages=history.copy()),
        ConversationContextBuilder(max_messages=2, max_characters=100),
    )

    observed = with_shadow.respond_to_text("지금 질문")
    control = without_shadow.respond_to_text("지금 질문")

    assert observed.text == control.text == "fake: 지금 질문"
    assert observed.shadow_turn_id is not None
    assert control.shadow_turn_id is None
    event = shadow.get(observed.shadow_turn_id)
    assert event.status == "pending"
    assert event.source == "cli"
    assert event.request.user_text == "지금 질문"
    assert event.request.context == (
        ConversationMessage("assistant", "바로 전 답"),
        ConversationMessage("user", "지금 질문"),
    )


def test_explicit_memory_turn_is_shadowed_without_running_the_chat_model(
    tmp_path: Path,
) -> None:
    from companion.memory import SqliteMemoryRepository

    shadow = SqliteTurnUnderstandingRepository(tmp_path / "turn.sqlite")
    response = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        memory_repository=SqliteMemoryRepository(tmp_path / "memory.sqlite"),
        turn_understanding_repository=shadow,
        turn_source="voice",
    ).respond_to_text("기억해. 나는 결론부터 듣는 걸 좋아해")

    assert response.shadow_turn_id is not None
    assert shadow.get(response.shadow_turn_id).source == "voice"
    assert response.memory_candidate_ids
