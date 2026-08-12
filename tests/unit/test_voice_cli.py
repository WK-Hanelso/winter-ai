import io
from pathlib import Path
import wave

import pytest

from companion import voice_cli
from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.contracts import AudioOutput, SpeechRequest
from companion.core import CompanionCore

IDENTITY = (
    '{"name":"겨울이","role":"companion","core_personality":["calm"],'
    '"values":["privacy"],"relationship_policy":["preserve continuity"],'
    '"immutable_boundaries":["do not impersonate real people"],"version":"1"}'
)


def one_wav(seconds: float = 0.05, rate: int = 22050) -> bytes:
    """A real, tiny wav.

    Placeholder bytes used to be enough. They are not any more: the CLI joins
    the pieces of an answer into one file, which means reading their format.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00" * (int(seconds * rate) * 2))
    return buffer.getvalue()


class RecordingTts:
    def __init__(self) -> None:
        self.requests: list[SpeechRequest] = []

    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        self.requests.append(request)
        return AudioOutput(data=one_wav(), media_type="audio/wav")


def core() -> CompanionCore:
    return CompanionCore(FakeChatModel(), InMemoryConversationRepository())


def test_default_style_is_the_conversation_profile() -> None:
    # `base` gives no instruction on ordinary turns, so the model answers like a
    # general assistant: paragraphs. `reference_broadcast` caps at one short
    # sentence and drops the question. The conversation profile is the one that
    # both answers and sounds like her, so the voice CLI defaults to it.
    assert voice_cli.build_parser().parse_args([]).style == "reference_conversation"


def test_speak_writes_a_wav_and_passes_the_planned_prosody(tmp_path: Path) -> None:
    tts = RecordingTts()

    path = voice_cli.speak(
        core(),
        tts,  # type: ignore[arg-type]
        "안녕",
        output_dir=tmp_path,
        pace=1.3,
        should_play=False,
    )

    assert path is not None
    assert path.suffix == ".wav"
    with wave.open(str(path)) as saved:
        assert saved.getnframes() > 0
    assert tts.requests[0].pace == 1.3


def test_speak_does_not_reach_the_sound_device_when_playback_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(path: Path) -> None:
        raise AssertionError("playback attempted with should_play=False")

    monkeypatch.setattr(voice_cli, "play", fail)

    voice_cli.speak(
        core(),
        RecordingTts(),  # type: ignore[arg-type]
        "안녕",
        output_dir=tmp_path,
        pace=1.0,
        should_play=False,
    )


def test_missing_identity_fails_instead_of_answering_as_a_generic_assistant(
    tmp_path: Path,
) -> None:
    assert voice_cli.main(["--identity-path", str(tmp_path / "absent.json"), "--say", "안녕"]) == 1


def test_player_reports_absence_rather_than_guessing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_cli.shutil, "which", lambda name: None)

    assert voice_cli.player() is None


def voice_arguments(tmp_path: Path) -> list[str]:
    """The voice files live in private storage, so tests point at stand-ins."""
    flow = tmp_path / "flow.pt"
    speaker = tmp_path / "speaker.pt"
    flow.write_bytes(b"")
    speaker.write_bytes(b"")
    return ["--flow-checkpoint", str(flow), "--speaker", str(speaker)]


def test_identity_is_loaded_into_the_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(IDENTITY)
    captured: dict[str, object] = {}

    def capture(core: object, tts: object, text: str, **keywords: object) -> None:
        captured.update(core=core, text=text, **keywords)

    monkeypatch.setattr(voice_cli, "speak", capture)
    monkeypatch.setattr(voice_cli, "CosyVoiceSpeechModel", lambda **keywords: RecordingTts())

    arguments = ["--identity-path", str(identity_path), "--say", "안녕", *voice_arguments(tmp_path)]
    assert voice_cli.main(arguments) == 0
    assert "겨울이" in captured["core"]._identity.system_message()  # type: ignore[union-attr]


def test_a_missing_voice_stops_before_answering(tmp_path: Path) -> None:
    # Without these the voice is not 겨울이's. Better to say so than to speak
    # in someone else's.
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(IDENTITY)

    assert voice_cli.main([
        "--identity-path", str(identity_path),
        "--say", "안녕",
        "--flow-checkpoint", str(tmp_path / "absent.pt"),
        "--speaker", str(tmp_path / "absent-speaker.pt"),
    ]) == 1


def test_each_sentence_is_synthesized_and_converted_separately(tmp_path: Path) -> None:
    # The whole point of the pipeline: sentence two is being said while sentence
    # one is being converted, which is only possible if they are separate calls.
    class TwoSentenceModel:
        def generate(self, request):  # type: ignore[no-untyped-def]
            from companion.contracts import ChatResult

            return ChatResult(text="응, 그랬구나. 근데 괜찮아?")

    tts = RecordingTts()
    converted: list[bytes] = []

    class RecordingConverter:
        def convert(self, audio: AudioOutput) -> AudioOutput:
            converted.append(audio.data)
            return AudioOutput(data=one_wav(0.03), media_type="audio/wav")

    path = voice_cli.speak(
        CompanionCore(TwoSentenceModel(), InMemoryConversationRepository()),  # type: ignore[arg-type]
        tts,  # type: ignore[arg-type]
        "오늘 발표 망쳤어",
        output_dir=tmp_path,
        pace=1.0,
        should_play=False,
        converter=RecordingConverter(),  # type: ignore[arg-type]
    )

    assert [request.text for request in tts.requests] == ["응, 그랬구나.", "근데 괜찮아?"]
    assert len(converted) == 2
    assert path is not None
    with wave.open(str(path)) as saved:
        # Both pieces plus the pause between them, rather than only the last.
        assert saved.getnframes() > int(0.03 * 2 * saved.getframerate())
