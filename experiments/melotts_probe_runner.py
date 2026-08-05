"""Container-side runner for an explicit Korean MeloTTS probe."""

from __future__ import annotations

import argparse
from pathlib import Path

from melo.api import TTS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    model = TTS(language="KR", device="cpu")
    model.tts_to_file(args.text, model.hps.data.spk2id["KR"], str(args.output_path), speed=1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
