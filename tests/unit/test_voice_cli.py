from pathlib import Path

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


class RecordingTts:
    def __init__(self) -> None:
        self.requests: list[SpeechRequest] = []

    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        self.requests.append(request)
        return AudioOutput(data=b"RIFFfake", media_type="audio/wav")


def core() -> CompanionCore:
    return CompanionCore(FakeChatModel(), InMemoryConversationRepository())


def test_default_style_is_base_not_the_reference_profile() -> None:
    # The Reference profile caps an answer at one short sentence, which
    # reproduces how the Reference speaks and answers questions badly. Getting
    # the companion running comes first; flipping this default is a decision,
    # not a detail.
    assert voice_cli.build_parser().parse_args([]).style == "base"


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
    assert path.read_bytes() == b"RIFFfake"
    assert path.suffix == ".wav"
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
