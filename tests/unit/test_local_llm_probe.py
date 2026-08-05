from pathlib import Path

import pytest

from experiments.local_llm_probe import build_docker_command


def test_probe_command_mounts_runtime_and_model_read_only(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    cli_path = runtime_dir / "llama-b10276" / "llama-cli"
    cli_path.parent.mkdir(parents=True)
    cli_path.touch()
    model_path = tmp_path / "models" / "qwen.gguf"
    model_path.parent.mkdir()
    model_path.touch()

    command = build_docker_command(
        runtime_dir=runtime_dir,
        model_path=model_path,
        image="winter-ai:dev",
        gpu_layers=99,
        context_size=2048,
        predict=64,
        prompt="안녕",
    )

    assert command[:6] == ["docker", "run", "--rm", "--gpus", "all", "--entrypoint"]
    assert f"{runtime_dir}:/runtime:ro" in command
    assert f"{model_path.parent}:/models:ro" in command
    assert command[-8:] == ["-c", "2048", "-n", "64", "-cnv", "-st", "-p", "안녕"]


def test_probe_command_rejects_missing_model(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    cli_path = runtime_dir / "llama-b10276" / "llama-cli"
    cli_path.parent.mkdir(parents=True)
    cli_path.touch()

    with pytest.raises(ValueError, match="GGUF model was not found"):
        build_docker_command(
            runtime_dir=runtime_dir,
            model_path=tmp_path / "missing.gguf",
            image="winter-ai:dev",
            gpu_layers=99,
            context_size=2048,
            predict=64,
            prompt="안녕",
        )
