"""Offline coverage for word-level speaker alignment and exchange pairing."""

from __future__ import annotations

import pytest

from companion.diarization import (
    UNASSIGNED_SPEAKER,
    DiarizationError,
    assign_words_to_speakers,
    build_exchanges,
    parse_spans,
    public_exchange_summary,
)
from companion.reference_subtitle_probe import SubtitleCue


def _words(*items: tuple[float, float, str]) -> tuple[SubtitleCue, ...]:
    return tuple(
        SubtitleCue(start_seconds=start, end_seconds=end, text=text)
        for start, end, text in items
    )


def test_words_are_grouped_into_turns_by_speaker() -> None:
    # The line that motivated all of this: one question, one answer.
    words = _words(
        (0.0, 0.5, "어떤"), (0.5, 1.0, "제품"), (1.0, 1.5, "찾으세요"),
        (2.0, 2.5, "그것"), (2.5, 3.0, "때문에요"),
    )
    spans = parse_spans(((0.0, 1.8, 0), (1.8, 3.0, 1)))

    turns = assign_words_to_speakers(words, spans)

    assert len(turns) == 2
    assert turns[0].speaker == 0
    assert turns[0].text == "어떤 제품 찾으세요"
    assert turns[1].speaker == 1
    assert turns[1].text == "그것 때문에요"


def test_a_reference_turn_is_paired_with_the_turn_before_it() -> None:
    words = _words(
        (0.0, 0.5, "요즘"), (0.5, 1.0, "어때"),
        (2.0, 2.5, "그냥"), (2.5, 3.0, "그래"),
    )
    spans = parse_spans(((0.0, 1.8, 0), (1.8, 3.0, 1)))

    exchanges = build_exchanges(assign_words_to_speakers(words, spans), 1)

    assert len(exchanges) == 1
    assert exchanges[0].prompt_text == "요즘 어때"
    assert exchanges[0].response_text == "그냥 그래"
    assert exchanges[0].prompt_speaker == 0


def test_unassigned_audio_between_turns_is_not_treated_as_a_prompt() -> None:
    # Silence and overlap are not something anyone said.
    words = _words(
        (0.0, 0.5, "요즘"), (0.5, 1.0, "어때"),
        (2.0, 2.5, "웅얼"), (2.5, 3.0, "웅얼"),
        (4.0, 4.5, "그냥"), (4.5, 5.0, "그래"),
    )
    spans = parse_spans(((0.0, 1.8, 0), (3.8, 5.0, 1)))

    turns = assign_words_to_speakers(words, spans)
    exchanges = build_exchanges(turns, 1)

    assert any(turn.speaker == UNASSIGNED_SPEAKER for turn in turns)
    assert len(exchanges) == 1
    assert exchanges[0].prompt_text == "요즘 어때"


def test_two_reference_turns_in_a_row_yield_one_exchange() -> None:
    # After answering, the Reference has no new prompt; the second turn is a
    # continuation, not a reply to anything.
    words = _words(
        (0.0, 0.5, "요즘"), (0.5, 1.0, "어때"),
        (2.0, 2.5, "그냥"), (2.5, 3.0, "그래"),
        (5.0, 5.5, "좀"), (5.5, 6.0, "피곤해"),
    )
    spans = parse_spans(((0.0, 1.8, 0), (1.8, 3.5, 1), (4.5, 6.0, 1)))

    exchanges = build_exchanges(assign_words_to_speakers(words, spans), 1)

    assert len(exchanges) == 1


def test_one_word_turns_are_too_thin_to_pair() -> None:
    words = _words((0.0, 0.5, "응"), (2.0, 2.5, "어"))
    spans = parse_spans(((0.0, 1.0, 0), (1.8, 3.0, 1)))

    assert build_exchanges(assign_words_to_speakers(words, spans), 1) == ()


def test_words_arriving_out_of_order_are_sorted_first() -> None:
    words = _words((2.0, 2.5, "둘"), (0.0, 0.5, "하나"))
    spans = parse_spans(((0.0, 3.0, 0),))

    turns = assign_words_to_speakers(words, spans)

    assert turns[0].text == "하나 둘"


def test_alignment_refuses_an_empty_word_list() -> None:
    with pytest.raises(DiarizationError, match="no words to assign"):
        assign_words_to_speakers((), parse_spans(((0.0, 1.0, 0),)))


def test_summary_counts_pairs_without_exposing_what_was_said() -> None:
    words = _words(
        (0.0, 0.5, "요즘"), (0.5, 1.0, "어때"),
        (2.0, 2.5, "그냥"), (2.5, 3.0, "그래"),
    )
    spans = parse_spans(((0.0, 1.8, 0), (1.8, 3.0, 1)))
    turns = assign_words_to_speakers(words, spans)

    summary = public_exchange_summary(turns, build_exchanges(turns, 1), 1)

    assert summary["exchange_count"] == 1
    assert summary["reference_words"] == 2
    assert "그냥" not in str(summary)
