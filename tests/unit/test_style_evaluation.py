"""Offline coverage for style distance."""

from __future__ import annotations

import pytest

from companion.reference_speech_style import (
    EndingProfile,
    SpeechStyleProfile,
    profile_transcript,
)
from companion.reference_subtitle_probe import SubtitleCue
from companion.style_evaluation import public_distance_summary, style_distance


def _cues(*texts: str) -> tuple[SubtitleCue, ...]:
    return tuple(
        SubtitleCue(float(i), float(i + 1), text) for i, text in enumerate(texts)
    )


_PLAIN_SHORT = _cues("밥 먹었어", "좋아", "그렇지", "몰라")
_POLITE_LONG = _cues(
    "오늘은 아침부터 아주 열심히 준비를 했습니다",
    "그래서 기분이 정말 좋았습니다",
    "다음에도 잘 부탁드립니다",
    "감사합니다",
)


def test_identical_profiles_have_zero_distance() -> None:
    profile = profile_transcript("a", _PLAIN_SHORT)

    result = style_distance(profile, profile_transcript("b", _PLAIN_SHORT))

    assert result.total_distance == 0.0
    assert result.is_complete


def test_opposite_register_and_length_produce_a_large_distance() -> None:
    near = style_distance(
        profile_transcript("plain", _PLAIN_SHORT),
        profile_transcript("ref", _PLAIN_SHORT),
    )
    far = style_distance(
        profile_transcript("polite", _POLITE_LONG),
        profile_transcript("ref", _PLAIN_SHORT),
    )

    assert far.total_distance > near.total_distance
    assert far.total_distance > 0.4


def test_register_carries_the_most_weight() -> None:
    result = style_distance(
        profile_transcript("polite", _POLITE_LONG),
        profile_transcript("ref", _PLAIN_SHORT),
    )

    weights = {trait.trait: trait.weight for trait in result.traits}
    assert weights["register"] == max(weights.values())


def test_an_uncomparable_trait_is_skipped_not_scored_as_agreement() -> None:
    # Noun fragments classify no endings, so register cannot be compared.
    fragments = profile_transcript("frag", _cues("안녕 세상", "오늘 연습"))

    result = style_distance(fragments, profile_transcript("ref", _PLAIN_SHORT))

    register = next(trait for trait in result.traits if trait.trait == "register")
    assert register.distance is None
    # A missing measurement must not be reported as a perfect match.
    assert not result.is_complete
    assert result.comparable_weight < 1.0


def test_distance_is_bounded_even_when_one_trait_is_wildly_off() -> None:
    verbose = profile_transcript(
        "verbose",
        _cues(" ".join(["단어"] * 200), " ".join(["단어"] * 200)),
    )

    result = style_distance(verbose, profile_transcript("ref", _PLAIN_SHORT))

    assert 0.0 <= result.total_distance <= 1.0


def _blank(label: str) -> SpeechStyleProfile:
    """A profile where no trait can be measured at all."""
    return SpeechStyleProfile(
        schema_version=1,
        label=label,
        utterance_count=1,
        word_count=1,
        character_count=1,
        mean_words_per_utterance=0.0,
        median_words_per_utterance=0.0,
        endings=EndingProfile(0, 0, 0, 1),
        filler_hits=0,
        filler_per_100_words=0.0,
        top_fillers=(),
        immediate_repetition_count=0,
        repetition_per_100_words=0.0,
        top_words=(),
    )


def test_both_zero_on_a_rate_counts_as_agreement_not_a_missing_value() -> None:
    # A speaker who never hesitates, matched by a companion that never
    # hesitates, is a perfect score for that trait.
    result = style_distance(
        profile_transcript("a", _PLAIN_SHORT), profile_transcript("b", _PLAIN_SHORT)
    )

    hesitation = next(trait for trait in result.traits if trait.trait == "hesitation")
    assert hesitation.distance == 0.0


def test_a_profile_without_classified_endings_still_scores_what_it_can() -> None:
    result = style_distance(_blank("a"), _blank("b"))

    # Length and hesitation always compare; register and ending mix cannot.
    assert not result.is_complete
    assert result.comparable_weight == pytest.approx(0.5)


def test_summary_reports_each_trait_and_completeness() -> None:
    result = style_distance(
        profile_transcript("polite", _POLITE_LONG),
        profile_transcript("ref", _PLAIN_SHORT),
    )

    summary = public_distance_summary(result)

    assert {trait["trait"] for trait in summary["traits"]} == {
        "register",
        "utterance_length",
        "hesitation",
        "ending_mix",
    }
    assert summary["is_complete"] is True
