"""Offline coverage for speaker verification decisions.

No audio, no model. Similarity scores are supplied directly so the decision
rules can be tested on their own.
"""

from __future__ import annotations

import pytest

from companion.speaker_verification import (
    MINIMUM_RELIABLE_SECONDS,
    SegmentScore,
    SpeakerVerificationError,
    cosine_similarity,
    describe,
    find_threshold,
    positive_control_rate,
    public_verification_summary,
    verify_segments,
)


def _scores(*values: float, duration: float = 3.0) -> tuple[SegmentScore, ...]:
    return tuple(
        SegmentScore(
            index=index,
            start_seconds=index * duration,
            end_seconds=index * duration + duration,
            similarity=value,
        )
        for index, value in enumerate(values)
    )


def test_cosine_similarity_matches_known_cases() -> None:
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
    assert cosine_similarity((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
    assert cosine_similarity((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(-1.0)


def test_cosine_similarity_refuses_degenerate_inputs() -> None:
    with pytest.raises(SpeakerVerificationError, match="same dimension"):
        cosine_similarity((1.0, 0.0), (1.0,))
    with pytest.raises(SpeakerVerificationError, match="must not be empty"):
        cosine_similarity((), ())
    with pytest.raises(SpeakerVerificationError, match="all zero"):
        cosine_similarity((0.0, 0.0), (1.0, 0.0))


def test_two_clear_speaker_groups_are_detected() -> None:
    # One cluster near 0.8, another near 0.3: two speakers.
    check = find_threshold(_scores(0.82, 0.79, 0.85, 0.80, 0.31, 0.28, 0.33, 0.30))

    assert check.is_bimodal
    assert check.threshold is not None
    assert 0.3 < check.threshold < 0.8
    assert check.low_count == 4
    assert check.high_count == 4


def test_a_single_tight_group_is_not_reported_as_two_speakers() -> None:
    # One speaker's natural spread must not be split into two.
    check = find_threshold(_scores(0.80, 0.82, 0.79, 0.81, 0.83, 0.78))

    assert not check.is_bimodal
    assert "one group" in check.reason


def test_a_lone_outlier_does_not_count_as_a_speaker_group() -> None:
    # One segment out of seven is 14% of the run, so a share test alone lets it
    # through. An absolute count is what actually rejects it.
    check = find_threshold(_scores(0.80, 0.82, 0.79, 0.81, 0.83, 0.78, 0.10))

    assert not check.is_bimodal
    assert "too few segments to be a speaker" in check.reason


def test_a_genuine_minority_speaker_is_still_detected() -> None:
    # A quiet participant holds few turns but must not be dismissed as noise.
    check = find_threshold(
        _scores(*([0.80] * 20), 0.30, 0.28, 0.32, 0.29)
    )

    assert check.is_bimodal
    assert check.low_count == 4


def test_too_few_segments_are_reported_rather_than_guessed() -> None:
    check = find_threshold(_scores(0.8, 0.3))

    assert not check.is_bimodal
    assert check.threshold is None
    assert "too few segments" in check.reason


def test_distribution_reports_spread_not_just_a_mean() -> None:
    distribution = describe(_scores(0.2, 0.4, 0.6, 0.8))

    assert distribution.count == 4
    assert distribution.mean == pytest.approx(0.5)
    assert distribution.median == pytest.approx(0.5)
    assert distribution.minimum == pytest.approx(0.2)
    assert distribution.maximum == pytest.approx(0.8)
    assert distribution.stdev > 0


def test_short_segments_are_counted_as_unreliable() -> None:
    short = SegmentScore(0, 0.0, MINIMUM_RELIABLE_SECONDS / 2, 0.9)
    long = SegmentScore(1, 0.0, MINIMUM_RELIABLE_SECONDS * 2, 0.9)

    assert not short.is_reliable
    assert long.is_reliable

    report = verify_segments("source-003", (short, long))
    assert report.short_count == 1
    assert report.reliable_count == 1


def test_positive_control_reports_the_accepted_share() -> None:
    scores = _scores(0.9, 0.85, 0.88, 0.2)

    assert positive_control_rate(scores, 0.5) == pytest.approx(0.75)


def test_positive_control_refuses_an_empty_run() -> None:
    with pytest.raises(SpeakerVerificationError, match="no segment scores"):
        positive_control_rate((), 0.5)


def test_summary_carries_the_failure_reason_not_only_a_threshold() -> None:
    report = verify_segments("source-003", _scores(0.80, 0.82, 0.79, 0.81, 0.83))

    summary = public_verification_summary(report)

    # A caller must be able to see that separation failed, not just read a
    # threshold that means nothing.
    assert summary["separation"]["is_bimodal"] is False
    assert summary["separation"]["reason"]
    assert summary["source_id"] == "source-003"
