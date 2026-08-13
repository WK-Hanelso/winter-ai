"""Cut the speech Chatterbox invents after the sentence has ended.

Given the same sentence three times, it returned 1.6, 3.3 and 4.3 seconds of
audio. The 1.6 is the sentence; the others carry one to three seconds of extra
speech after it, which 천우 heard as "뒤에 뭔가 붙어서 나오는데 중국어 같다".
It is a known fault of the model rather than of the text — the issue tracker has
it for short segments (resemble-ai/chatterbox#97, #271) and the reports say the
generation parameters do not reliably fix it.

So it is cut off here, and the cut is decided by the text rather than by taste:
we know how many syllables were asked for, and roughly how long the model takes
to say one. Audio well past that is not the sentence.

The rule
--------
Find the pauses. Keep the shortest prefix that still covers the expected
duration, cutting at a pause rather than mid-word. When nothing looks wrong,
nothing is cut — a sentence that runs slightly long is a sentence, not a
hallucination.

This also buys time twice over: the invented tail was being sent through voice
conversion as well, which is the slowest stage in the path.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
import wave

sys.path.append(str(Path(__file__).resolve().parent))

from trim_leaked_opening import frame_levels  # noqa: E402

# Measured over the generations that came back the right length: 5.1 to 7.0
# syllables a second. The low end is used, so "expected" is generous and the
# trim stays cautious.
SYLLABLES_PER_SECOND = 5.0
# Below the clip's own speech level by this much counts as a pause. Same
# threshold as the opening trim, and for the same reason: the gap is not subtle.
SILENCE_BELOW_SPEECH_DB = 35.0
# Shorter than this is phrasing inside a sentence, not the end of one.
MINIMUM_PAUSE_SECONDS = 0.30
# Never cut this close to the expected end. A sentence's own final syllable can
# sit just past the estimate, and losing it is far worse than keeping a second
# of nonsense.
GRACE = 1.25


def syllables(text: str) -> int:
    """Hangul syllables. What the estimate is built on; other scripts read at
    a different rate and are not counted rather than counted wrongly."""
    return sum(1 for character in text if "가" <= character <= "힣")


def find_tail_cut(
    levels: Sequence[float],
    frame_seconds: float,
    expected_seconds: float,
) -> float | None:
    """Where to end the audio, or None to keep all of it."""
    if not levels:
        return None
    total = len(levels) * frame_seconds
    limit = expected_seconds * GRACE
    if total <= limit:
        return None

    speech = sorted(levels)[int(len(levels) * 0.9)]
    threshold = speech - SILENCE_BELOW_SPEECH_DB
    minimum_frames = max(1, int(MINIMUM_PAUSE_SECONDS / frame_seconds))

    # Walk the pauses in order and stop at the first one past the point where
    # the sentence could have finished. Later pauses belong to the invention.
    run_start: int | None = None
    for index, level in enumerate(levels):
        if level <= threshold:
            if run_start is None:
                run_start = index
            continue
        if run_start is not None and index - run_start >= minimum_frames:
            pause_middle = (run_start + index) / 2 * frame_seconds
            if pause_middle >= expected_seconds:
                return pause_middle
        run_start = None
    # Trailing silence is not a hallucination, but it is not speech either.
    if run_start is not None and len(levels) - run_start >= minimum_frames:
        return run_start * frame_seconds
    return None


def trim(samples: Sequence[int], rate: int, text: str) -> tuple[list[int], float]:
    """Return the kept samples and how many seconds were dropped."""
    count = syllables(text)
    if not count:
        return list(samples), 0.0
    levels, frame = frame_levels(list(samples), rate)
    cut = find_tail_cut(levels, frame / rate, count / SYLLABLES_PER_SECOND)
    if cut is None:
        return list(samples), 0.0
    keep = int(cut * rate)
    return list(samples[:keep]), (len(samples) - keep) / rate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    with wave.open(str(arguments.audio)) as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    import array

    samples = array.array("h")
    samples.frombytes(raw)
    kept, dropped = trim(samples, rate, arguments.text)
    print(
        f"{len(samples) / rate:.1f}초 → {len(kept) / rate:.1f}초 "
        f"({dropped:.1f}초 잘라냄)"
    )
    if arguments.output:
        with wave.open(str(arguments.output), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(array.array("h", kept).tobytes())
        arguments.output.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
