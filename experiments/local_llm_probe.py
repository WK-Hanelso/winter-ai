"""Run the selected local Qwen3 GGUF probe inside the project Docker image.

This is an explicit, manual model probe.  It never downloads a model and it
does not provide a fake fallback when Docker, GPU access, or the runtime fails.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
from typing import Sequence


DEFAULT_PROMPT = "한국어로 한 문장만 답해줘. 로컬 AI 컴패니언의 역할을 설명해줘."


def build_docker_command(
    *,
    runtime_dir: Path,
    model_path: Path,
    image: str,
    gpu_layers: int,
    context_size: int,
    predict: int,
    prompt: str,
) -> list[str]:
    cli_path = runtime_dir / "llama-b10276" / "llama-cli"
    if not cli_path.is_file():
        raise ValueError(f"llama-cli was not found at {cli_path}")
    if not model_path.is_file():
        raise ValueError(f"GGUF model was not found at {model_path}")

    container_cli = Path("/runtime") / cli_path.relative_to(runtime_dir)
    return [
        "docker",
        "run",
        "--rm",
        "--gpus",
        "all",
        "--entrypoint",
        str(container_cli),
        "-v",
        f"{runtime_dir}:/runtime:ro",
        "-v",
        f"{model_path.parent}:/models:ro",
        image,
        "-m",
        f"/models/{model_path.name}",
        "-ngl",
        str(gpu_layers),
        "-c",
        str(context_size),
        "-n",
        str(predict),
        "-cnv",
        "-st",
        "-p",
        prompt,
    ]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--image", default="winter-ai:dev")
    parser.add_argument("--gpu-layers", type=int, default=99)
    parser.add_argument("--context-size", type=int, default=2048)
    parser.add_argument("--predict", type=int, default=64)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    command = build_docker_command(
        runtime_dir=args.runtime_dir.resolve(),
        model_path=args.model_path.resolve(),
        image=args.image,
        gpu_layers=args.gpu_layers,
        context_size=args.context_size,
        predict=args.predict,
        prompt=args.prompt,
    )
    print("Running:", shlex.join(command), flush=True)
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
