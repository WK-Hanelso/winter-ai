import pytest

from companion.adapters.fake import (
    AdapterUnavailableError,
    FakeAudioPlayer,
    FakeAudioRecorder,
    FakeChatModel,
    FakeSpeechToText,
    FakeTextToSpeech,
    InMemoryConversationRepository,
)
from companion.cli.orchestration import CliOrchestrator
from companion.contracts import AudioInput
from companion.core import CompanionCore
from companion.ports import SpeechToText
from companion.voice.orchestration import VoiceOrchestrator


def test_cli_and_voice_share_one_core_and_conversation_repository() -> None:
    repository = InMemoryConversationRepository()
    core = CompanionCore(FakeChatModel(), repository)
    cli = CliOrchestrator(core)
    voice = VoiceOrchestrator(core, FakeSpeechToText("음성 입력"), FakeTextToSpeech())

    assert cli.handle_text("텍스트 입력").text == "fake: 텍스트 입력"
    assert voice.handle_audio(AudioInput(b"audio", "audio/fake")).data == "fake: 음성 입력".encode()
    assert [(message.role, message.content) for message in repository.list_messages()] == [
        ("user", "텍스트 입력"),
        ("assistant", "fake: 텍스트 입력"),
        ("user", "음성 입력"),
        ("assistant", "fake: 음성 입력"),
    ]


class FailingSpeechToText:
    def transcribe(self, audio: AudioInput):
        raise AdapterUnavailableError("fake stt is unavailable")


def test_voice_propagates_stt_failure_without_calling_core() -> None:
    repository = InMemoryConversationRepository()
    voice = VoiceOrchestrator(CompanionCore(FakeChatModel(), repository), FailingSpeechToText(), FakeTextToSpeech())

    with pytest.raises(AdapterUnavailableError, match="stt is unavailable"):
        voice.handle_audio(AudioInput(b"audio", "audio/fake"))
    assert repository.list_messages() == ()


def test_voice_push_to_talk_uses_recorder_core_tts_and_player() -> None:
    repository = InMemoryConversationRepository()
    voice = VoiceOrchestrator(CompanionCore(FakeChatModel(), repository), FakeSpeechToText(), FakeTextToSpeech())
    player = FakeAudioPlayer()

    voice.push_to_talk(FakeAudioRecorder(), player)

    assert player.played[0].data == b"fake: fake transcript"
    assert [(message.role, message.content) for message in repository.list_messages()] == [
        ("user", "fake transcript"),
        ("assistant", "fake: fake transcript"),
    ]
