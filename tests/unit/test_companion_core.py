import pytest

from companion.adapters.fake import (
    AdapterUnavailableError,
    FailingChatModel,
    FakeChatModel,
    InMemoryConversationRepository,
)
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.identity import CompanionIdentity
from companion.memory import ActiveMemoryRetriever, SqliteMemoryRepository


class CapturingChatModel:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        return ChatResult(text="captured")


def test_core_returns_structured_response_and_persists_a_turn() -> None:
    repository = InMemoryConversationRepository()
    core = CompanionCore(FakeChatModel(), repository)

    response = core.respond_to_text("안녕")

    assert response.text == "fake: 안녕"
    assert response.dialogue_act == "answer"
    assert response.prosody.emotion == "neutral"
    assert [(message.role, message.content) for message in repository.list_messages()] == [
        ("user", "안녕"),
        ("assistant", "fake: 안녕"),
    ]


def test_core_does_not_persist_an_assistant_message_when_model_fails() -> None:
    repository = InMemoryConversationRepository()
    core = CompanionCore(FailingChatModel(), repository)

    with pytest.raises(AdapterUnavailableError):
        core.respond_to_text("안녕")

    assert [(message.role, message.content) for message in repository.list_messages()] == [
        ("user", "안녕")
    ]


def test_core_sends_bounded_repository_context_to_the_chat_model() -> None:
    repository = InMemoryConversationRepository(
        _messages=[
            ConversationMessage(role="user", content="첫 대화"),
            ConversationMessage(role="assistant", content="첫 응답"),
        ]
    )
    chat_model = CapturingChatModel()
    core = CompanionCore(
        chat_model,
        repository,
        ConversationContextBuilder(max_messages=2, max_characters=100),
    )

    core.respond_to_text("현재 질문")

    # System blocks are asserted elsewhere; this test is about the conversation
    # window staying inside its budget.
    conversation = tuple(
        message for message in chat_model.requests[0].messages if message.role != "system"
    )
    assert conversation == (
        ConversationMessage(role="assistant", content="첫 응답"),
        ConversationMessage(role="user", content="현재 질문"),
    )


def test_core_prefixes_identity_as_system_message() -> None:
    model = CapturingChatModel()
    identity = CompanionIdentity(
        "Winter",
        "companion",
        ("calm",),
        ("honesty",),
        ("respect",),
        ("no impersonation",),
        "1",
    )
    core = CompanionCore(model, InMemoryConversationRepository(), identity=identity)
    core.respond_to_text("안녕")
    # Identity leads: it frames every other system block.
    assert model.requests[0].messages[0].role == "system"
    assert "You are Winter." in model.requests[0].messages[0].content


def test_core_injects_selected_active_memory_as_distinct_system_message(tmp_path) -> None:
    repository = InMemoryConversationRepository()
    memory_repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    memory = memory_repository.add_candidate(
        kind="preference", content="천우는 Python config를 선호한다"
    )
    memory_repository.transition(memory.id, "approved")
    memory_repository.transition(memory.id, "active")
    model = CapturingChatModel()
    core = CompanionCore(
        model, repository, memory_retriever=ActiveMemoryRetriever(memory_repository)
    )
    core.respond_to_text("Python config는?")
    system_messages = [
        message for message in model.requests[0].messages if message.role == "system"
    ]
    assert any(memory.id in message.content for message in system_messages)


def test_core_creates_candidate_only_for_explicit_memory_request(tmp_path) -> None:
    memory_repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        memory_repository=memory_repository,
    )

    response = core.respond_to_text("기억해. 나는 Python config를 선호해")

    assert len(response.memory_candidate_ids) == 1
    assert response.dialogue_act == "memory_candidate"
    assert response.prosody.emotion == "warm"
    assert response.verbal_style.tone == "warm"
    assert "기억 후보로 저장했어. 검토 후 활성화할 수 있어." in response.text
    candidate = memory_repository.get(response.memory_candidate_ids[0])
    assert (candidate.content, candidate.status, candidate.source) == (
        "나는 Python config를 선호해",
        "candidate",
        "user_explicit",
    )
    assert memory_repository.list_active() == ()


def test_core_does_not_create_candidate_for_ordinary_or_empty_request(tmp_path) -> None:
    memory_repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        memory_repository=memory_repository,
    )

    assert core.respond_to_text("Python config를 선호해").memory_candidate_ids == ()
    assert core.respond_to_text("기억해").memory_candidate_ids == ()
    assert memory_repository.list() == ()


def test_system_messages_are_ordered_identity_memory_style_grounding(tmp_path) -> None:
    memory_repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    memory = memory_repository.add_candidate(
        kind="preference", content="천우는 Python config를 선호한다"
    )
    memory_repository.transition(memory.id, "approved")
    memory_repository.transition(memory.id, "active")
    identity = CompanionIdentity(
        "Winter", "companion", ("calm",), ("honesty",), ("respect",), ("no impersonation",), "1"
    )
    model = CapturingChatModel()
    core = CompanionCore(
        model,
        InMemoryConversationRepository(),
        identity=identity,
        memory_retriever=ActiveMemoryRetriever(memory_repository),
    )

    core.respond_to_text("Python config는?")

    system_messages = [
        message for message in model.requests[0].messages if message.role == "system"
    ]
    assert len(system_messages) == 4
    # Identity frames everything; grounding sits closest to the generated turn
    # because it is the constraint that must not be dropped.
    assert "You are Winter." in system_messages[0].content
    assert memory.id in system_messages[1].content
    # Not "반말": the register is drawn per turn, so asserting on it would fail
    # whenever the polite draw came up. The length rule is in every style block.
    assert "문장 이내로" in system_messages[2].content
    assert "지어내지 마" in system_messages[3].content
