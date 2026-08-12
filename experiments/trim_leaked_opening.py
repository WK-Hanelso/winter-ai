"""Cut the reference speech the model prepends to what it was asked to say.

CosyVoice interleaves the reference transcript with the reference audio at a
fixed ratio learned during training. That ratio is language-dependent, so on
Korean the model misjudges where the reference audio ends and produces a short
burst of the reference before starting the requested sentence. It is an upstream
bug — issue #967 — with no prompt-level fix; a matching transcript and a better
transcriber shrink it and do not remove it.

Found by silence, not by transcription
--------------------------------------
Two other approaches failed and the reasons are worth keeping. Correlating the
opening against the reference audio finds nothing, because the model *generates*
the leaked part rather than splicing it. Transcribing the output and locating
the requested sentence finds nothing either, because what remains after the
transcript fix is too short to be recognised as words at all.

What it is, measured on a real output: 0.2 s of sound, then 0.8 s of silence at
-90 dB, then the sentence. So the leak is whatever precedes the first real pause,
and a cut inside that pause is inaudible.

The rule is therefore: if the clip opens with sound, and a long silence follows
within the first couple of seconds, cut in the middle of that silence. If the
clip opens with silence there was no leak, and nothing is cut.
"""

from __future__ import annotations

import argparse
import array
from collections.abc import Sequence
import math
from pathlib import Path
import sys
import wave

FRAME_SECONDS = 0.02
# A pause this long is a boundary rather than a stop between syllables.
MINIMUM_GAP_SECONDS = 0.25
# A leak lives at the very start. A silence found later is ordinary phrasing.
MAXIMUM_LEAD_SECONDS = 2.5
# Frames this far below the clip's speech level count as silence. The gap in a
# real example sat 60 dB down, so this is not a delicate threshold.
SILENCE_BELOW_SPEECH_DB = 35.0


def read_wave(path: Path) -> tuple[array.array, int, int]:
    with wave.open(str(path)) as handle:
        samples = array.array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))
        return samples, handle.getframerate(), handle.getnchannels()


def frame_levels(samples: Sequence[int], rate: int) -> tuple[list[float], int]:
    frame = max(1, int(FRAME_SECONDS * rate))
    levels = []
    for start in range(0, len(samples) - frame, frame):
        energy = math.sqrt(math.fsum(v * v for v in samples[start : start + frame]) / frame)
        levels.append(20 * math.log10(max(energy, 1e-9) / 32768))
    return levels, frame


def find_cut(levels: Sequence[float], frame_seconds: float) -> float | None:
    """Middle of the first real pause, if the clip opens with sound before it."""
    if not levels:
        return None
    speech = sorted(levels)[int(len(levels) * 0.9)]
    threshold = speech - SILENCE_BELOW_SPEECH_DB
    minimum_frames = max(1, int(MINIMUM_GAP_SECONDS / frame_seconds))
    limit = int(MAXIMUM_LEAD_SECONDS / frame_seconds)

    if levels[0] <= threshold:
        # Opens quiet: nothing was prepended.
        return None

    run_start = None
    for index, level in enumerate(levels[:limit]):
        if level <= threshold:
            if run_start is None:
                run_start = index
            elif index - run_start + 1 >= minimum_frames:
                # Cut in the middle of the pause rather than at either edge, so
                # neither the leak's tail nor the sentence's onset is clipped.
                run_end = index
                while run_end + 1 < len(levels) and levels[run_end + 1] <= threshold:
                    run_end += 1
                return (run_start + run_end) / 2 * frame_seconds
        else:
            run_start = None
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="합성 결과 앞에 붙은 참조 발화를 잘라냅니다.")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--maximum-trim-seconds",
        type=float,
        default=2.5,
        help="이보다 많이 자르라는 결과는 판정 실패로 보고 자르지 않습니다.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    samples, rate, channels = read_wave(arguments.audio)
    mono = samples if channels == 1 else samples[::channels]
    levels, frame = frame_levels(mono, rate)
    total = len(mono) / rate

    cut = find_cut(levels, frame / rate)
    if cut is None:
        print(f"앞에 붙은 소리가 없습니다. 그대로 둡니다 ({total:.1f}초).")
        arguments.output.write_bytes(arguments.audio.read_bytes())
        arguments.output.chmod(0o600)
        return 0
    if cut > arguments.maximum_trim_seconds:
        print(f"{cut:.2f}초를 자르라는 결과는 신뢰하지 않습니다.", file=sys.stderr)
        return 1

    start = int(cut * rate) * channels
    with wave.open(str(arguments.output), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples[start:].tobytes())
    arguments.output.chmod(0o600)
    print(f"{cut:.2f}초에서 자름 | {total:.1f}초 → {total - cut:.1f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
