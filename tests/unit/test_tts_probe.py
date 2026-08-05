from pathlib import Path

from experiments.tts_probe import build_command


def test_tts_probe_uses_separate_cache_and_output_mounts(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    output_path = tmp_path / "audio" / "output.wav"

    command = build_command(cache_dir, output_path, "안녕")

    assert f"{cache_dir}:/root/.cache:rw" in command
    assert f"{output_path.parent}:/output:rw" in command
    assert command[-4:] == ["--text", "안녕", "--output-path", "/output/output.wav"]
