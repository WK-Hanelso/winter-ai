"""Run a manual, local Whisper.cpp transcription probe in Docker.

The default is an explicit CPU run.  Requesting CUDA never falls back to CPU:
Docker or the runtime failure is returned to the caller unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
from typing import Sequence


IMAGES = {
    "cpu": "ghcr.io/ggml-org/whisper.cpp:main-vulkan",
    "cuda": "ghcr.io/ggml-org/whisper.cpp:main-cuda",
}


def build_docker_command(
    *,
    model_path: Path,
    audio_path: Path,
    device: str,
    image: str | None,
    language: str,
) -> list[str]:
    if not model_path.is_file():
        raise ValueError(f"Whisper model was not found at {model_path}")
    if not audio_path.is_file():
        raise ValueError(f"audio fixture was not found at {audio_path}")

    command = ["docker", "run", "--rm"]
    if device == "cuda":
        command.extend(["--gpus", "all"])
    command.extend(
        [
            "--entrypoint",
            "whisper-cli",
            "-v",
            f"{model_path.parent}:/models:ro",
            "-v",
            f"{audio_path.parent}:/audio:ro",
            image or IMAGES[device],
            "-m",
            f"/models/{model_path.name}",
            "-f",
            f"/audio/{audio_path.name}",
            "-l",
            language,
            "-nt",
            "-np",
        ]
    )
    if device == "cpu":
        command.append("-ng")
    return command


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--audio-path", type=Path, required=True)
    parser.add_argument("--device", choices=tuple(IMAGES), default="cpu")
    parser.add_argument("--image")
    parser.add_argument("--language", default="ko")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    command = build_docker_command(
        model_path=args.model_path.resolve(),
        audio_path=args.audio_path.resolve(),
        device=args.device,
        image=args.image,
        language=args.language,
    )
    print("Running:", shlex.join(command), flush=True)
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
