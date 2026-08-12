from pathlib import Path
import subprocess

import pytest

from companion.adapters.cosyvoice import IMAGE, RUNNER, CosyVoiceSpeechModel
from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import SpeechRequest


def model(tmp_path: Path, **overrides: object) -> CosyVoiceSpeechModel:
    flow = tmp_path / "flow" / "epoch_29_whole.pt"
    speaker = tmp_path / "voice" / "winter-speaker.pt"
    for path in (flow, speaker):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return CosyVoiceSpeechModel(
        flow_checkpoint=flow,
        speaker=speaker,
        runner_dir=tmp_path / "experiments",
        **overrides,  # type: ignore[arg-type]
    )


def request(text: str = "안녕") -> SpeechRequest:
    return SpeechRequest(text=text, emotion="neutral", pace=1.0, energy=0.5, pitch_offset=0.0)


def test_the_command_carries_the_settings_that_were_chosen_by_ear(tmp_path: Path) -> None:
    # Each of these was picked by comparing recordings. A default creeping back
    # in changes the voice without changing any code.
    command = model(tmp_path, pace=1.12).command(tmp_path, tmp_path / "text.txt")

    assert IMAGE in command
    assert RUNNER in command
    assert command[command.index("--speed") + 1] == "1.12"
    assert "--no-text-frontend" in command
    assert command[command.index("--instruction") + 1] == ""


def test_the_saved_speaker_is_used_rather_than_rebuilt(tmp_path: Path) -> None:
    # Rebuilding means embedding every clip, about fourteen seconds a turn.
    command = model(tmp_path).command(tmp_path, tmp_path / "text.txt")

    assert command[command.index("--load-speaker") + 1] == "/speaker/winter-speaker.pt"
    assert "--clips" not in command


def test_missing_voice_files_are_named(tmp_path: Path) -> None:
    speech = model(tmp_path)
    speech.flow_checkpoint.unlink()

    with pytest.raises(AdapterUnavailableError) as error:
        speech.synthesize(request())

    assert "epoch_29_whole.pt" in str(error.value)


def test_empty_text_is_rejected_before_starting_a_container(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        model(tmp_path).synthesize(request(text="   "))


def test_synthesis_returns_the_written_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        staging = Path(next(item for item in command if item.endswith(":/work:rw")).split(":")[0])
        (staging / "speech-01.wav").write_bytes(b"RIFFfake")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("companion.adapters.cosyvoice.subprocess.run", fake_run)

    audio = model(tmp_path).synthesize(request())

    assert audio.data == b"RIFFfake"
    assert audio.media_type == "audio/wav"


def test_a_zero_exit_without_a_file_is_still_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "companion.adapters.cosyvoice.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, b"", b""),
    )

    with pytest.raises(AdapterUnavailableError):
        model(tmp_path).synthesize(request())
