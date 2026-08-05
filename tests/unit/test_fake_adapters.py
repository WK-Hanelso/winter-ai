import pytest

from companion.adapters.fake import (
    AdapterUnavailableError,
    FailingChatModel,
    FakeChatModel,
    FakeSpeechToText,
    FakeTextToSpeech,
    InMemoryConversationRepository,
)
from companion.contracts import AudioInput, ChatRequest, ConversationMessage, SpeechRequest


def test_fake_chat_model_is_deterministic() -> None:
    model = FakeChatModel()
    request = ChatRequest(prompt="안녕")
    assert model.generate(request).text == model.generate(request).text == "fake: 안녕"


def test_failing_model_surfaces_an_error_without_fallback() -> None:
    with pytest.raises(AdapterUnavailableError, match="unavailable"):
        FailingChatModel().generate(ChatRequest(prompt="안녕"))


def test_fake_speech_adapters_round_trip_text_deterministically() -> None:
    transcript = FakeSpeechToText(transcript="테스트").transcribe(
        AudioInput(data=b"ignored", media_type="audio/fake")
    )
    audio = FakeTextToSpeech().synthesize(SpeechRequest(text=transcript.text))
    assert audio.data == "테스트".encode("utf-8")
    assert audio.media_type == "audio/fake"


def test_in_memory_repository_preserves_message_order() -> None:
    repository = InMemoryConversationRepository()
    first = ConversationMessage(role="user", content="첫 메시지")
    second = ConversationMessage(role="assistant", content="둘째 메시지")
    repository.append(first)
    repository.append(second)
    assert repository.list_messages() == (first, second)
