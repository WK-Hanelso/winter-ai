"""Container-side runner: remove background noise from Reference clips.

One model load for a whole directory. Loading it per clip would dominate the
run, and there are 130 of them.

``--self-test`` runs the model over a generated signal at build time. It proves
the weights are present and the graph executes, which is the part that has
silently not been true in every other image in this project at least once.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from df.enhance import enhance, init_df, load_audio, save_audio
import numpy as np
import torch

# DeepFilterNet operates at 48 kHz. The clips were cut at 48 kHz, so nothing is
# resampled on the way through.
MODEL_RATE = 48_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reference 클립의 배경 잡음을 제거합니다.")
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="모델이 실제로 돌아가는지만 확인하고 끝냅니다.",
    )
    return parser


def self_test() -> int:
    model, state, _ = init_df()
    if state.sr() != MODEL_RATE:
        print(f"예상 밖의 sample rate: {state.sr()}", file=sys.stderr)
        return 1
    # A second of a tone plus noise. Only that enhance() returns audio of the
    # right shape is checked; how much it removed is not this test's business.
    time = np.arange(MODEL_RATE) / MODEL_RATE
    hiss = np.random.default_rng(0).normal(0, 0.05, time.size)
    noisy = np.sin(2 * np.pi * 220 * time) * 0.3 + hiss
    cleaned = enhance(model, state, torch.tensor(noisy, dtype=torch.float32).unsqueeze(0))
    if cleaned.shape[-1] != time.size:
        print(f"출력 길이가 다릅니다: {cleaned.shape}", file=sys.stderr)
        return 1
    print(f"denoise self-test OK ({state.sr()} Hz)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.self_test:
        return self_test()
    if arguments.input_dir is None or arguments.output_dir is None:
        print("--input-dir 과 --output-dir 이 필요합니다.", file=sys.stderr)
        return 1

    clips = sorted(arguments.input_dir.glob("*.wav"))
    if not clips:
        print(f"클립이 없습니다: {arguments.input_dir}", file=sys.stderr)
        return 1

    model, state, _ = init_df()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    arguments.output_dir.chmod(0o700)

    for clip in clips:
        audio, _ = load_audio(str(clip), sr=state.sr())
        save_audio(clip.name, enhance(model, state, audio), state.sr(),
                   output_dir=str(arguments.output_dir))
        (arguments.output_dir / clip.name).chmod(0o600)
        print(".", end="", flush=True)
    print(f"\n클립 {len(clips)}개 → {arguments.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
