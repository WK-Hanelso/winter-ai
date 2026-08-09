"""Offline coverage for speech-style extraction.

Every utterance here is synthetic Korean written for this test.
"""

from __future__ import annotations

import pytest

from companion.reference_speech_style import (
    ReferenceSpeechStyleError,
    compare_profiles,
    profile_transcript,
    public_style_summary,
)
from companion.reference_subtitle_probe import SubtitleCue


def _cues(*texts: str) -> tuple[SubtitleCue, ...]:
    return tuple(
        SubtitleCue(float(index), float(index + 1), text)
        for index, text in enumerate(texts)
    )


def test_formal_endings_are_not_counted_as_plain() -> None:
    # "습니다" also ends with "다"; the formal group has to win.
    profile = profile_transcript("t", _cues("연습을 했습니다", "밥을 먹었습니다"))

    assert profile.endings.polite_formal == 2
    assert profile.endings.plain == 0


def test_casual_polite_and_plain_endings_are_separated() -> None:
    profile = profile_transcript(
        "t", _cues("연습을 했어요", "밥을 먹었지", "좋네요", "그렇구나")
    )

    assert profile.endings.polite_casual == 2
    assert profile.endings.plain == 2
    assert profile.endings.polite_ratio == 0.5


def test_polite_ratio_is_absent_when_no_ending_classifies() -> None:
    # Korean words, but nothing that ends like a sentence: noun fragments are
    # common in a live broadcast where the speaker trails off.
    profile = profile_transcript("t", _cues("안녕 세상", "오늘 연습"))

    assert profile.endings.classified == 0
    assert profile.endings.unclassified == 2
    assert profile.endings.polite_ratio is None


def test_trailing_punctuation_does_not_block_ending_classification() -> None:
    profile = profile_transcript("t", _cues("연습을 했어요!"))

    assert profile.endings.polite_casual == 1


def test_utterances_split_on_sentence_punctuation_when_present() -> None:
    profile = profile_transcript("t", _cues("밥을 먹었어요. 그리고 잤어요."))

    assert profile.utterance_count == 2


def test_utterances_fall_back_to_cue_boundaries_without_punctuation() -> None:
    # Older automatic captions carry no punctuation at all.
    profile = profile_transcript("t", _cues("밥을 먹었어요", "그리고 잤어요"))

    assert profile.utterance_count == 2


def test_filler_rate_counts_whole_words_only() -> None:
    # "그것" and "어제" contain filler characters but are ordinary words.
    profile = profile_transcript("t", _cues("어 그 약간 그것은 어제 일이야"))

    assert profile.filler_hits == 3
    assert dict(profile.top_fillers)["어"] == 1


def test_immediate_repetition_is_counted_inside_an_utterance() -> None:
    profile = profile_transcript("t", _cues("진짜 진짜 좋아요"))

    assert profile.immediate_repetition_count == 1


def test_repetition_across_an_utterance_boundary_is_not_counted() -> None:
    # A word repeated across cues is more likely a transcription seam than a
    # spoken repair.
    profile = profile_transcript("t", _cues("좋아요", "좋아요"))

    assert profile.immediate_repetition_count == 0


def test_top_words_exclude_fillers_and_single_characters() -> None:
    profile = profile_transcript(
        "t", _cues("연습 연습을 했어요 그 어 음", "연습 정말 좋아요")
    )

    top = dict(profile.top_words)
    assert "연습" in top
    assert "그" not in top
    assert "어" not in top


def test_median_utterance_length_uses_word_counts() -> None:
    profile = profile_transcript("t", _cues("하나", "하나 둘 셋", "하나 둘"))

    assert profile.median_words_per_utterance == 2.0


def test_empty_and_wordless_transcripts_are_refused() -> None:
    with pytest.raises(ReferenceSpeechStyleError, match="no cues"):
        profile_transcript("t", ())
    with pytest.raises(ReferenceSpeechStyleError, match="no usable utterances"):
        profile_transcript("t", _cues("...", "!!"))
    with pytest.raises(ReferenceSpeechStyleError, match="no Korean words"):
        profile_transcript("t", _cues("hello there"))


def test_top_n_must_be_positive() -> None:
    with pytest.raises(ReferenceSpeechStyleError, match="at least 1"):
        profile_transcript("t", _cues("좋아요"), top_n=0)


def test_summary_reports_word_frequencies_but_no_whole_utterances() -> None:
    profile = profile_transcript(
        "t", _cues("오늘은 연습을 아주 열심히 했어요 그래서 기분이 좋아요")
    )

    serialized = str(public_style_summary(profile))

    assert "연습을" in serialized  # a frequency entry is the point of the probe
    assert "오늘은 연습을 아주" not in serialized  # the utterance itself is not


def test_comparison_reports_shared_traits_and_ratios() -> None:
    primary = profile_transcript("whisper", _cues("연습 연습을 했어요 그"))
    secondary = profile_transcript("service", _cues("연습 연습을 했어요"))

    comparison = compare_profiles(primary, secondary)

    assert comparison["primary"] == "whisper"
    assert "연습을" in comparison["shared_top_words"]
    assert comparison["word_count_ratio"] is not None


def test_comparison_handles_a_secondary_without_classified_endings() -> None:
    primary = profile_transcript("whisper", _cues("좋아요"))
    secondary = profile_transcript("service", _cues("hello 세상"))

    assert compare_profiles(primary, secondary)["polite_ratio_difference"] is None
