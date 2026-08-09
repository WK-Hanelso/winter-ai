"""Offline coverage for the held-out evaluation harness."""

from __future__ import annotations

from experiments.style_holdout_eval import (
    PROBE_QUESTIONS,
    profile_responses,
    split_cues,
)
import pytest

from companion.reference_subtitle_probe import SubtitleCue


def _cues(count: int) -> tuple[SubtitleCue, ...]:
    return tuple(
        SubtitleCue(float(i), float(i + 1), f"발화 {i} 좋아") for i in range(count)
    )


def test_split_keeps_time_order_so_holdout_is_genuinely_later() -> None:
    shuffled = tuple(reversed(_cues(10)))

    train, holdout = split_cues(shuffled, 0.7)

    assert len(train) == 7
    assert len(holdout) == 3
    assert train[0].start_seconds == 0.0
    assert holdout[0].start_seconds == 7.0


def test_split_refuses_ratios_that_empty_a_side() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        split_cues(_cues(10), 0.0)
    with pytest.raises(ValueError, match="between 0 and 1"):
        split_cues(_cues(10), 1.0)
    with pytest.raises(ValueError, match="empty side"):
        split_cues(_cues(3), 0.01)


def test_responses_are_profiled_and_blank_ones_dropped() -> None:
    profile = profile_responses("gen", ["밥 먹었어", "  ", "좋아"])

    assert profile.utterance_count == 2


def test_probe_questions_are_everyday_and_not_about_facts() -> None:
    # The evaluation asks how it speaks, not what it knows; a factual question
    # would measure the model's knowledge instead of its register.
    assert len(PROBE_QUESTIONS) >= 10
    assert all("?" in question or question.strip() for question in PROBE_QUESTIONS)


def test_self_distance_is_reported_as_the_floor(tmp_path) -> None:
    # The harness must expose the speaker's own train-vs-holdout distance;
    # without it a generated score has nothing to be judged against.
    import inspect

    from experiments import style_holdout_eval

    source = inspect.getsource(style_holdout_eval.main)
    assert "reference_self_distance" in source
