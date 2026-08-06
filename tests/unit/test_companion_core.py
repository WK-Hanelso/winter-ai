import pytest

from companion.adapters.fake import (
    AdapterUnavailableError,
    FailingChatModel,
    FakeChatModel,
    InMemoryConversationRepository,
)
from companion.core import CompanionCore
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ChatResult, ConversationMessage


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

    assert chat_model.requests[0].messages == (
        ConversationMessage(role="assistant", content="첫 응답"),
        ConversationMessage(role="user", content="현재 질문"),
    )
