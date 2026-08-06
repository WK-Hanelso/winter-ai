"""Synthesize the same Korean sentence with every Voice Identity profile."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
import wave

from companion.voice_profile import load_voice_identity
from experiments.tts_probe import build_command

DEFAULT_TEXT = "천우님, 겨울이가 Python, Docker, Qwen 작업 상태를 차분히 정리할게요."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, plan in load_voice_identity().profiles.items():
        output = args.output_dir / f"{name}.wav"
        started = time.monotonic()
        code = subprocess.run(build_command(args.cache_dir.resolve(), output.resolve(), args.text, plan.pace), check=False).returncode
        duration = 0.0
        if code == 0:
            with wave.open(str(output), "rb") as audio: duration = audio.getnframes() / audio.getframerate()
        results.append({"profile": name, "request": {"emotion": plan.emotion, "pace": plan.pace, "energy": plan.energy, "pitch_offset": plan.pitch_offset}, "exit_code": code, "duration_seconds": duration, "synthesis_seconds": time.monotonic() - started, "wav": output.name})
    (args.output_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    return 0 if all(result["exit_code"] == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
