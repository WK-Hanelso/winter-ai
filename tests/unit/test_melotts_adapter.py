from pathlib import Path
import subprocess

import pytest

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.melotts import CACHE_MOUNT, IMAGE, MeloTtsSpeechModel
from companion.contracts import SpeechRequest


def request(text: str = "안녕", pace: float = 1.0) -> SpeechRequest:
    return SpeechRequest(text=text, emotion="neutral", pace=pace, energy=0.5, pitch_offset=0.0)


def test_command_mounts_the_cache_and_redirects_every_home_cache(tmp_path: Path) -> None:
    model = MeloTtsSpeechModel(cache_dir=tmp_path / "cache", user="1000:1000")
    (tmp_path / "cache").mkdir()

    command = model.command(tmp_path / "out" / "speech.wav", "안녕", 1.0)

    assert command[:5] == ["docker", "run", "--rm", "--user", "1000:1000"]
    assert f"{(tmp_path / 'cache').resolve()}:{CACHE_MOUNT}:rw" in command
    assert command[command.index(IMAGE) + 1 :] == [
        "--text", "안녕",
        "--output-path", "/output/speech.wav",
        "--speed", "1.0",
    ]
    # Every cache the image would otherwise write under /root, which a --user
    # run cannot traverse. Missing NUMBA_CACHE_DIR is the one that fails loudest.
    for variable in ("HOME", "HF_HOME", "NUMBA_CACHE_DIR", "MPLCONFIGDIR"):
        assignment = next(item for item in command if item.startswith(f"{variable}="))
        assert assignment.split("=", 1)[1].startswith(CACHE_MOUNT)


def test_command_omits_user_when_unset(tmp_path: Path) -> None:
    model = MeloTtsSpeechModel(cache_dir=tmp_path, user=None)

    assert "--user" not in model.command(tmp_path / "speech.wav", "안녕", 1.0)


def test_empty_text_is_rejected_before_starting_a_container(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        MeloTtsSpeechModel(cache_dir=tmp_path).synthesize(request(text="   "))


def test_synthesis_returns_the_written_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        mount = next(item for item in command if item.endswith(":/output:rw"))
        staging = Path(mount.split(":/output:rw")[0])
        name = Path(command[command.index("--output-path") + 1]).name
        (staging / name).write_bytes(b"RIFFfake")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("companion.adapters.melotts.subprocess.run", fake_run)

    audio = MeloTtsSpeechModel(cache_dir=tmp_path).synthesize(request())

    assert audio.data == b"RIFFfake"
    assert audio.media_type == "audio/wav"


def test_container_failure_surfaces_the_last_stderr_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, b"", b"noise\nRuntimeError: no locator\n")

    monkeypatch.setattr("companion.adapters.melotts.subprocess.run", fake_run)

    with pytest.raises(AdapterUnavailableError) as error:
        MeloTtsSpeechModel(cache_dir=tmp_path).synthesize(request())

    assert "RuntimeError: no locator" in str(error.value)


def test_a_zero_exit_without_a_file_is_still_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("companion.adapters.melotts.subprocess.run", fake_run)

    with pytest.raises(AdapterUnavailableError):
        MeloTtsSpeechModel(cache_dir=tmp_path).synthesize(request())
