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
    identify_reference_speaker,
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


def test_windows_overlap_so_a_speaker_change_lands_inside_one_of_them() -> None:
    from companion.speaker_verification import iter_windows

    windows = iter_windows(0.0, 4.5, window_seconds=1.5, hop_seconds=0.75)

    assert windows[0] == (0.0, 1.5)
    assert windows[1] == (0.75, 2.25)
    # Consecutive windows share half their length.
    assert windows[1][0] < windows[0][1]


def test_a_span_shorter_than_one_window_is_still_scored() -> None:
    from companion.speaker_verification import iter_windows

    # Dropping short cues would silently discard the shortest turns, which in a
    # conversation are exactly the interesting ones.
    assert iter_windows(10.0, 10.8, window_seconds=1.5) == ((10.0, 10.8),)


def test_the_tail_of_a_long_span_is_not_left_unscored() -> None:
    from companion.speaker_verification import iter_windows

    windows = iter_windows(0.0, 4.0, window_seconds=1.5, hop_seconds=0.75)

    assert windows[-1][1] == 4.0


def test_windows_reject_impossible_parameters() -> None:
    from companion.speaker_verification import SpeakerVerificationError, iter_windows

    with pytest.raises(SpeakerVerificationError, match="must be positive"):
        iter_windows(0.0, 1.0, window_seconds=0.0)
    with pytest.raises(SpeakerVerificationError, match="span must be positive"):
        iter_windows(5.0, 5.0)


def _vec(*values: float) -> tuple[float, ...]:
    return values


def test_two_means_finds_the_two_groups_and_is_deterministic() -> None:
    from companion.speaker_verification import two_means

    points = (
        _vec(1.0, 0.0), _vec(0.98, 0.2), _vec(0.95, 0.1),
        _vec(0.0, 1.0), _vec(0.2, 0.98), _vec(0.1, 0.95),
    )

    labels, _ = two_means(points)
    again, _ = two_means(points)

    assert labels == again  # a random seed would make runs disagree
    assert len(set(labels)) == 2
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4] == labels[5]


def test_clustering_labels_the_group_nearer_the_enrolment_as_the_reference() -> None:
    from companion.speaker_verification import label_clusters

    speaker_a = (_vec(1.0, 0.0), _vec(0.98, 0.2), _vec(0.95, 0.1), _vec(0.99, 0.05))
    speaker_b = (_vec(0.0, 1.0), _vec(0.2, 0.98), _vec(0.1, 0.95), _vec(0.05, 0.99))

    split = label_clusters(speaker_a + speaker_b, _vec(1.0, 0.0))

    reference = split.centroid_similarities[split.reference_index]
    other = split.centroid_similarities[1 - split.reference_index]
    assert reference > other
    assert split.is_usable


def test_a_split_with_no_clear_winner_is_marked_unusable() -> None:
    from companion.speaker_verification import label_clusters

    # Enrolment sits exactly between the two groups: it cannot name either.
    speaker_a = (_vec(1.0, 0.0), _vec(0.98, 0.2), _vec(0.95, 0.1), _vec(0.99, 0.05))
    speaker_b = (_vec(0.0, 1.0), _vec(0.2, 0.98), _vec(0.1, 0.95), _vec(0.05, 0.99))

    split = label_clusters(speaker_a + speaker_b, _vec(0.7071, 0.7071))

    assert not split.is_usable


def test_a_tiny_second_group_is_marked_unusable() -> None:
    from companion.speaker_verification import label_clusters

    points = tuple(_vec(1.0, 0.0) for _ in range(20)) + (_vec(0.0, 1.0),)

    assert not label_clusters(points, _vec(1.0, 0.0)).is_usable


def test_run_lengths_measure_consecutive_stretches() -> None:
    from companion.speaker_verification import run_lengths

    assert run_lengths((0, 0, 0, 1, 1, 0)) == (3, 2, 1)
    assert run_lengths((0,)) == (1,)


def test_alternating_labels_are_rejected_as_turn_taking() -> None:
    from companion.speaker_verification import label_clusters

    # Two groups interleaved every window is noise sitting inside one speaker,
    # not a conversation: nobody swaps turns twice a second.
    alternating = tuple(
        _vec(1.0, 0.0) if index % 2 == 0 else _vec(0.0, 1.0) for index in range(24)
    )

    split = label_clusters(alternating, _vec(1.0, 0.0))

    assert split.mean_run_windows < 2.0
    assert not split.is_usable


def test_blocked_labels_are_accepted_as_turn_taking() -> None:
    from companion.speaker_verification import label_clusters

    blocked = tuple(_vec(1.0, 0.0) for _ in range(12)) + tuple(
        _vec(0.0, 1.0) for _ in range(12)
    )

    split = label_clusters(blocked, _vec(1.0, 0.0))

    assert split.mean_run_windows >= 2.0
    assert split.is_usable


def test_the_speaker_closest_to_the_enrolment_is_named() -> None:
    from companion.speaker_verification import identify_reference_speaker

    result = identify_reference_speaker(
        {0: _vec(0.1, 1.0), 1: _vec(1.0, 0.1)},
        {0: 60.0, 1: 60.0},
        _vec(1.0, 0.0),
    )

    assert result.reference_speaker == 1
    assert result.is_confident


def test_two_equally_close_speakers_are_refused_rather_than_guessed() -> None:
    from companion.speaker_verification import identify_reference_speaker

    result = identify_reference_speaker(
        {0: _vec(1.0, 0.0), 1: _vec(1.0, 0.0)},
        {0: 60.0, 1: 60.0},
        _vec(1.0, 0.0),
    )

    assert result.reference_speaker is None
    assert not result.is_confident
    assert "no speaker leads" in result.reason


def test_a_speaker_with_seconds_of_audio_is_skipped_not_ranked() -> None:
    from companion.speaker_verification import identify_reference_speaker

    # A centroid from 1.6 seconds describes that noise, not a person.
    result = identify_reference_speaker(
        {0: _vec(0.1, 1.0), 1: _vec(1.0, 0.1), 3: _vec(1.0, 0.0)},
        {0: 60.0, 1: 60.0, 3: 1.6},
        _vec(1.0, 0.0),
    )

    assert result.skipped_speakers == (3,)
    assert result.reference_speaker == 1


def test_a_single_remaining_speaker_cannot_be_confirmed() -> None:
    from companion.speaker_verification import identify_reference_speaker

    result = identify_reference_speaker(
        {0: _vec(1.0, 0.0), 1: _vec(0.0, 1.0)},
        {0: 60.0, 1: 2.0},
        _vec(1.0, 0.0),
    )

    assert result.reference_speaker is None
    assert "nothing to compare" in result.reason


def test_identification_summary_carries_the_reason() -> None:
    from companion.speaker_verification import (
        identify_reference_speaker,
        public_identification_summary,
    )

    summary = public_identification_summary(
        identify_reference_speaker(
            {0: _vec(0.1, 1.0), 1: _vec(1.0, 0.1)}, {0: 60.0, 1: 60.0}, _vec(1.0, 0.0)
        )
    )

    assert summary["reference_speaker"] == 1
    assert summary["reason"]


def test_zero_length_cues_are_dropped_before_windowing() -> None:
    from companion.reference_subtitle_probe import SubtitleCue
    from companion.speaker_verification import audible_cues

    # Word-level transcription gives some words an identical start and end.
    cues = (
        SubtitleCue(0.0, 1.0, "있다"),
        SubtitleCue(4.0, 4.0, "굉장한"),
        SubtitleCue(5.0, 6.0, "또있다"),
    )

    kept = audible_cues(cues)

    assert [cue.text for cue in kept] == ["있다", "또있다"]


def test_dropping_empty_cues_leaves_windowing_valid() -> None:
    from companion.reference_subtitle_probe import SubtitleCue
    from companion.speaker_verification import audible_cues, iter_windows

    cues = (SubtitleCue(4.0, 4.0, "0초"), SubtitleCue(0.0, 3.0, "정상"))

    # Every surviving cue must be safe to window; the zero-length one is not.
    for cue in audible_cues(cues):
        assert iter_windows(cue.start_seconds, cue.end_seconds)


def test_lone_speaker_is_refused_without_a_threshold() -> None:
    """생략하면 예전 동작 그대로. 생각하지 않은 호출자가 조용히 채택하지 않는다."""
    result = identify_reference_speaker(
        {0: (1.0, 0.0)}, {0: 60.0}, (1.0, 0.0)
    )

    assert result.reference_speaker is None
    assert "nothing to compare against" in result.reason


def test_lone_speaker_is_accepted_above_the_threshold() -> None:
    """혼자 말하는 녹음이 비교 상대가 없다는 이유만으로 버려지지 않는다.

    007·008·009에서 단일 화자 chunk가 0.7177, 0.7263으로 나왔는데, 상대가 있던
    곳에서 채택된 1등이 0.6868이었다. 더 닮은 쪽이 버려지고 있었다.
    """
    result = identify_reference_speaker(
        {0: (1.0, 0.0)}, {0: 60.0}, (1.0, 0.0), lone_speaker_similarity=0.65
    )

    assert result.reference_speaker == 0
    assert result.margin is None


def test_lone_speaker_below_the_threshold_is_still_refused() -> None:
    """임계값은 문을 여는 것이지 없애는 것이 아니다."""
    result = identify_reference_speaker(
        {0: (0.0, 1.0)}, {0: 60.0}, (1.0, 0.0), lone_speaker_similarity=0.65
    )

    assert result.reference_speaker is None
    assert "below" in result.reason


def test_accepted_lone_speaker_is_confident() -> None:
    """골라놓고 확신은 아니라고 하면 클립 절단이 건너뛴다.

    is_confident가 이유 문자열까지 대조하던 때 실제로 그렇게 됐다. 유사도
    0.6945로 채택된 chunk가 is_confident False로 나와 버려질 뻔했다.
    """
    result = identify_reference_speaker(
        {0: (1.0, 0.0)}, {0: 60.0}, (1.0, 0.0), lone_speaker_similarity=0.65
    )

    assert result.reference_speaker == 0
    assert result.is_confident
