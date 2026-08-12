"""The trim must remove the leaked reference and nothing else."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments"))

from trim_leaked_opening import find_cut  # noqa: E402

FRAME = 0.02
LOUD = 0.0
QUIET = -60.0


def levels(*runs: tuple[float, float]) -> list[float]:
    """Build a level track from (seconds, level) runs."""
    track: list[float] = []
    for seconds, level in runs:
        track.extend([level] * int(seconds / FRAME))
    return track


def test_a_leaked_opening_before_a_pause_is_cut() -> None:
    # Half a second of reference speech, a pause, then the sentence.
    track = levels((0.5, LOUD), (0.4, QUIET), (3.0, LOUD))

    cut = find_cut(track, FRAME)

    assert cut is not None
    assert 0.5 < cut < 0.9


def test_a_pause_inside_a_short_sentence_is_not_a_leak() -> None:
    # "그래, 그래." — a comma's pause a third of the way into 1.5 seconds. The
    # trim used to take everything before it and hand back half a sentence.
    track = levels((0.4, LOUD), (0.3, QUIET), (0.8, LOUD))

    assert find_cut(track, FRAME) is None


def test_a_clip_that_opens_quietly_is_left_alone() -> None:
    assert find_cut(levels((0.3, QUIET), (2.0, LOUD)), FRAME) is None
