from pathlib import Path

import pytest

from companion.adapters.fake import (
    AdapterUnavailableError,
    FakeChatModel,
    FakeSpeechToText,
    FakeTextToSpeech,
    InMemoryConversationRepository,
)
from companion.cli.orchestration import CliOrchestrator
from companion.contracts import AudioInput
from companion.core import CompanionCore
from companion.memory import SqliteMemoryRepository
from companion.turn_understanding import SqliteTurnUnderstandingRepository
from companion.voice.orchestration import VoiceOrchestrator
from companion.winter_events import SqliteWinterEventRepository


class FixedClock:
    def now(self):  # type: ignore[no-untyped-def]
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime(2026, 8, 18, 9, tzinfo=ZoneInfo("Asia/Seoul"))


def test_cli_and_voice_share_one_core_and_conversation_repository(tmp_path: Path) -> None:
    repository = InMemoryConversationRepository()
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    winter_events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    core = CompanionCore(
        FakeChatModel(),
        repository,
        turn_understanding_repository=turns,
        winter_event_repository=winter_events,
        clock=FixedClock(),
    )
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
    assert [event.request.user_text for event in turns.list(status="pending")] == [
        "텍스트 입력",
        "음성 입력",
    ]
    day = winter_events.list_day("2026-08-18")
    assert [event.source for event in day] == ["unknown", "unknown"]
    assert all("text" not in dict(event.metadata) for event in day)


class FailingSpeechToText:
    def transcribe(self, audio: AudioInput):
        raise AdapterUnavailableError("fake stt is unavailable")


def test_voice_propagates_stt_failure_without_calling_core() -> None:
    repository = InMemoryConversationRepository()
    voice = VoiceOrchestrator(
        CompanionCore(FakeChatModel(), repository),
        FailingSpeechToText(),
        FakeTextToSpeech(),
    )

    with pytest.raises(AdapterUnavailableError, match="stt is unavailable"):
        voice.handle_audio(AudioInput(b"audio", "audio/fake"))
    assert repository.list_messages() == ()


def test_cli_and_voice_activate_explicit_memories_through_the_same_core(tmp_path) -> None:
    memory_repository = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        memory_repository=memory_repository,
    )
    cli = CliOrchestrator(core)
    voice = VoiceOrchestrator(
        core,
        FakeSpeechToText("기억해. Voice에서도 후보를 저장해"),
        FakeTextToSpeech(),
    )

    cli_response = cli.handle_text("기억해. CLI에서도 후보를 저장해")
    voice_output = voice.handle_audio(AudioInput(b"audio", "audio/fake"))

    assert len(cli_response.memory_candidate_ids) == 1
    assert [(memory.content, memory.status) for memory in memory_repository.list()] == [
        ("CLI에서도 후보를 저장해", "active"),
        ("Voice에서도 후보를 저장해", "active"),
    ]
    assert "기억해둘게." in voice_output.data.decode()
