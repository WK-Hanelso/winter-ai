from pathlib import Path

from experiments.stt_probe import build_docker_command


def test_cpu_probe_disables_gpu_explicitly(tmp_path: Path) -> None:
    model_path = tmp_path / "models" / "ggml-small.bin"
    audio_path = tmp_path / "audio" / "sample.flac"
    model_path.parent.mkdir()
    audio_path.parent.mkdir()
    model_path.touch()
    audio_path.touch()

    command = build_docker_command(
        model_path=model_path,
        audio_path=audio_path,
        device="cpu",
        image=None,
        language="ko",
    )

    assert "--gpus" not in command
    assert command[-1] == "-ng"
    assert "ghcr.io/ggml-org/whisper.cpp:main-vulkan" in command


def test_cuda_probe_requires_gpu_without_cpu_fallback(tmp_path: Path) -> None:
    model_path = tmp_path / "models" / "ggml-small.bin"
    audio_path = tmp_path / "audio" / "sample.flac"
    model_path.parent.mkdir()
    audio_path.parent.mkdir()
    model_path.touch()
    audio_path.touch()

    command = build_docker_command(
        model_path=model_path,
        audio_path=audio_path,
        device="cuda",
        image=None,
        language="ko",
    )

    assert command[3:5] == ["--gpus", "all"]
    assert "-ng" not in command
    assert "ghcr.io/ggml-org/whisper.cpp:main-cuda" in command
