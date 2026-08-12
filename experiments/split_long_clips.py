"""Split clips that are too long for the card, instead of discarding them.

seed-vc's loader accepts up to 30 seconds, and this GPU does not: a batch of two
25-second clips runs the 6 GiB card out of memory in the DiT feed-forward. The
first instinct is to cap the clip length, but capping at 15 s throws away 7 of
the 25 minutes we just went to some trouble to collect.

Nothing about voice conversion needs a clip to be a whole sentence — it learns
what she sounds like, not what she said. So a long clip is cut into pieces that
fit, at the quietest point in the region where a cut is allowed, which lands in
a pause rather than mid-vowel.

A trailing piece shorter than ``--min-seconds`` is dropped: below a second the
loader skips it anyway, and a fragment that short carries more edge than voice.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import wave

import numpy as np

# 20 ms frames. Long enough to average out a glottal pulse, short enough to find
# the bottom of a pause rather than the middle of the word beside it.
FRAME_SECONDS = 0.02


def read_wave(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"16-bit mono만 지원합니다: {path}")
        rate = handle.getframerate()
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    return data, rate


def write_wave(path: Path, samples: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.astype(np.int16).tobytes())
    path.chmod(0o600)


def quietest_point(samples: np.ndarray, rate: int, low: float, high: float) -> int:
    """Sample index of the quietest frame between ``low`` and ``high`` seconds."""
    frame = max(1, int(FRAME_SECONDS * rate))
    start = int(low * rate)
    end = min(len(samples), int(high * rate))
    if end - start < frame * 2:
        return end
    window = samples[start:end].astype(np.float64)
    usable = (len(window) // frame) * frame
    energy = np.abs(window[:usable].reshape(-1, frame)).mean(axis=1)
    return start + int(np.argmin(energy)) * frame


def split(samples: np.ndarray, rate: int, minimum: float, maximum: float) -> list[np.ndarray]:
    pieces: list[np.ndarray] = []
    remaining = samples
    while len(remaining) > maximum * rate:
        # Search the second half of the allowed span: cutting as late as the
        # limit permits keeps the piece count down, and a pause is likelier
        # somewhere in that range than exactly at the limit.
        cut = quietest_point(remaining, rate, maximum * 0.6, maximum)
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    if len(remaining) >= minimum * rate:
        pieces.append(remaining)
    return pieces


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-seconds", type=float, default=1.5)
    parser.add_argument("--max-seconds", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    arguments.output_dir.chmod(0o700)

    untouched = 0
    produced = 0
    dropped_seconds = 0.0
    kept_seconds = 0.0
    for path in sorted(arguments.input_dir.glob("*.wav")):
        samples, rate = read_wave(path)
        seconds = len(samples) / rate
        if seconds <= arguments.max_seconds:
            shutil.copy2(path, arguments.output_dir / path.name)
            (arguments.output_dir / path.name).chmod(0o600)
            untouched += 1
            kept_seconds += seconds
            continue
        pieces = split(samples, rate, arguments.min_seconds, arguments.max_seconds)
        for index, piece in enumerate(pieces):
            write_wave(arguments.output_dir / f"{path.stem}-p{index}.wav", piece, rate)
            kept_seconds += len(piece) / rate
        produced += len(pieces)
        dropped_seconds += seconds - sum(len(piece) for piece in pieces) / rate

    print(
        f"그대로 {untouched}개, 쪼개서 {produced}개 → 총 {untouched + produced}개, "
        f"{kept_seconds / 60:.1f}분 (버린 꼬리 {dropped_seconds:.1f}초)"
    )
    print(f"저장: {arguments.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
