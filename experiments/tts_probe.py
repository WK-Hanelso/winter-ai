"""Run a manual Korean MeloTTS probe in its pinned Docker image."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess


DEFAULT_TEXT = "안녕하세요. 로컬 컴패니언의 음성 출력 경로를 확인하고 있습니다."


def build_command(cache_dir: Path, output_path: Path, text: str) -> list[str]:
    return [
        "docker", "run", "--rm",
        "-v", f"{cache_dir}:/root/.cache:rw",
        "-v", f"{output_path.parent}:/output:rw",
        "winter-ai:melotts-probe",
        "--text", text,
        "--output-path", f"/output/{output_path.name}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_command(args.cache_dir.resolve(), args.output_path.resolve(), args.text)
    print("Running:", shlex.join(command), flush=True)
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
