"""Offline coverage for rejoining turns split by a breath."""

from __future__ import annotations

import pytest

from companion.diarization import (
    UNASSIGNED_SPEAKER,
    DiarizationError,
    SpeakerTurn,
    bridge_short_gaps,
    build_exchanges,
)


def _turn(speaker: int, start: float, end: float, text: str) -> SpeakerTurn:
    return SpeakerTurn(
        speaker=speaker,
        start_seconds=start,
        end_seconds=end,
        text=text,
        word_count=len(text.split()),
    )


def test_a_breath_inside_one_utterance_is_bridged() -> None:
    # The pattern that made questions arrive as two words.
    turns = (
        _turn(0, 0.0, 2.0, "그때 오디션에서"),
        _turn(UNASSIGNED_SPEAKER, 2.0, 2.3, "..."),
        _turn(0, 2.3, 4.0, "어떤 노래 부르셨어요"),
    )

    merged = bridge_short_gaps(turns)

    assert len(merged) == 1
    assert merged[0].text == "그때 오디션에서 어떤 노래 부르셨어요"
    assert merged[0].word_count == 5  # 2 + 3


def test_a_long_silence_is_a_real_boundary() -> None:
    turns = (
        _turn(0, 0.0, 2.0, "첫 발화"),
        _turn(UNASSIGNED_SPEAKER, 2.0, 8.0, "긴 무음"),
        _turn(0, 8.0, 10.0, "다른 발화"),
    )

    merged = bridge_short_gaps(turns, maximum_gap_seconds=1.0)

    assert len(merged) == 3


def test_a_gap_between_different_speakers_is_never_bridged() -> None:
    # Bridging here would put two people's words in one utterance.
    turns = (
        _turn(0, 0.0, 2.0, "진행자 말"),
        _turn(UNASSIGNED_SPEAKER, 2.0, 2.2, "."),
        _turn(1, 2.2, 4.0, "겨울이 말"),
    )

    merged = bridge_short_gaps(turns)

    assert len(merged) == 3
    assert [turn.speaker for turn in merged] == [0, UNASSIGNED_SPEAKER, 1]


def test_adjacent_turns_of_one_speaker_are_joined() -> None:
    turns = (_turn(0, 0.0, 2.0, "앞"), _turn(0, 2.0, 4.0, "뒤"))

    merged = bridge_short_gaps(turns)

    assert len(merged) == 1
    assert merged[0].text == "앞 뒤"


def test_bridging_restores_the_full_question_in_a_pair() -> None:
    turns = (
        _turn(0, 0.0, 2.0, "그때 오디션에서"),
        _turn(UNASSIGNED_SPEAKER, 2.0, 2.3, "..."),
        _turn(0, 2.3, 4.0, "어떤 노래 부르셨어요"),
        _turn(1, 4.5, 6.0, "가로수 그늘 아래 서면 불렀어요"),
    )

    fragmented = build_exchanges(turns, 1)
    whole = build_exchanges(bridge_short_gaps(turns), 1)

    # Without bridging the question is only its tail.
    assert fragmented[0].prompt_text == "어떤 노래 부르셨어요"
    assert whole[0].prompt_text == "그때 오디션에서 어떤 노래 부르셨어요"


def test_bridging_refuses_an_empty_turn_list() -> None:
    with pytest.raises(DiarizationError, match="no turns to bridge"):
        bridge_short_gaps(())
