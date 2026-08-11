"""Rank clips by how quiet their background is.

A live broadcast carries the room with it — this one was recorded in the rain,
and she says so on air. Whatever is behind the voice gets learned as part of the
voice, so the noise floor decides which clips are worth using before anything
else about them does.

Measured, not guessed: the quietest tenth of short frames approximates the floor
between words, and the loudest tenth approximates the speech. Their ratio is a
usable stand-in for signal-to-noise without needing to know where the words are.

This only ranks. Whether the best of them is clean enough is something a person
has to hear.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import wave

import numpy as np

# 20 ms frames: long enough to be stable, short enough that a gap between words
# lands in its own frames rather than being averaged into speech.
FRAME_SECONDS = 0.020
QUIET_PERCENTILE = 10
LOUD_PERCENTILE = 90


@dataclass(frozen=True)
class Measurement:
    name: str
    seconds: float
    floor_db: float
    speech_db: float

    @property
    def separation_db(self) -> float:
        """How far the speech sits above the background."""
        return self.speech_db - self.floor_db


def read_wave(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as handle:
        raw = handle.readframes(handle.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float64), handle.getframerate()


def decibels(value: float) -> float:
    # Full scale is 32768 for 16-bit audio, so 0 dB here means "as loud as the
    # format allows" and everything real is negative.
    return 20.0 * float(np.log10(max(value, 1e-9) / 32768.0))


def measure(path: Path) -> Measurement:
    samples, rate = read_wave(path)
    frame = int(FRAME_SECONDS * rate)
    usable = len(samples) - len(samples) % frame
    frames = samples[:usable].reshape(-1, frame)
    energies = np.sqrt((frames**2).mean(axis=1))
    return Measurement(
        name=path.stem,
        seconds=len(samples) / rate,
        floor_db=decibels(float(np.percentile(energies, QUIET_PERCENTILE))),
        speech_db=decibels(float(np.percentile(energies, LOUD_PERCENTILE))),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="클립을 배경 잡음 기준으로 정렬합니다.")
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--show", type=int, default=15)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    paths = sorted(arguments.clips.glob("*.wav"))
    if not paths:
        print(f"클립이 없습니다: {arguments.clips}", file=sys.stderr)
        return 1

    measurements = [measure(path) for path in paths]
    measurements.sort(key=lambda item: item.separation_db, reverse=True)

    separations = [item.separation_db for item in measurements]
    print(f"클립 {len(measurements)}개")
    print(
        f"음성-배경 차이  최고 {separations[0]:.1f} dB | "
        f"중앙 {separations[len(separations) // 2]:.1f} dB | "
        f"최저 {separations[-1]:.1f} dB"
    )
    print(f"\n{'클립':<32} {'초':>5} {'배경':>7} {'음성':>7} {'차이':>7}")
    for item in measurements[: arguments.show]:
        print(
            f"{item.name:<32} {item.seconds:>5.1f} {item.floor_db:>7.1f} "
            f"{item.speech_db:>7.1f} {item.separation_db:>7.1f}"
        )

    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps(
                [
                    {
                        "name": item.name,
                        "seconds": round(item.seconds, 3),
                        "floor_db": round(item.floor_db, 2),
                        "speech_db": round(item.speech_db, 2),
                        "separation_db": round(item.separation_db, 2),
                    }
                    for item in measurements
                ],
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        arguments.report.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
