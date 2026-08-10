"""Offline coverage for applying a speaker timeline to transcript cues."""

from __future__ import annotations

import pytest

from companion.diarization import (
    UNASSIGNED_SPEAKER,
    DiarizationError,
    parse_prediction,
    parse_spans,
    public_diarization_summary,
    split_cues_by_speaker,
    summarise_speakers,
)
from companion.reference_subtitle_probe import SubtitleCue


def _cue(start: float, end: float, text: str = "발화") -> SubtitleCue:
    return SubtitleCue(start_seconds=start, end_seconds=end, text=text)


def test_a_cue_holding_two_speakers_is_split_at_the_change() -> None:
    # This is the whole point: one transcript line, a question and its answer.
    cues = (_cue(0.0, 10.0, "- 어떤 제품 찾으세요? - 그것 때문에요."),)
    spans = parse_spans(((0.0, 4.0, 0), (4.0, 10.0, 1)))

    pieces = split_cues_by_speaker(cues, spans)

    assert len(pieces) == 2
    assert pieces[0].speaker == 0
    assert pieces[1].speaker == 1
    assert all(piece.was_split for piece in pieces)
    assert pieces[0].end_seconds == 4.0


def test_a_cue_inside_one_speaker_is_left_whole() -> None:
    cues = (_cue(1.0, 5.0),)
    spans = parse_spans(((0.0, 8.0, 0),))

    pieces = split_cues_by_speaker(cues, spans)

    assert len(pieces) == 1
    assert not pieces[0].was_split
    assert (pieces[0].start_seconds, pieces[0].end_seconds) == (1.0, 5.0)


def test_split_pieces_keep_the_whole_text_and_say_so() -> None:
    # Dividing the words by timing alone would invent an alignment the
    # transcript does not contain.
    cues = (_cue(0.0, 10.0, "두 사람의 말"),)
    spans = parse_spans(((0.0, 4.0, 0), (4.0, 10.0, 1)))

    pieces = split_cues_by_speaker(cues, spans)

    assert {piece.text for piece in pieces} == {"두 사람의 말"}
    assert all(piece.was_split for piece in pieces)


def test_audio_nobody_speaks_over_is_kept_and_marked_unassigned() -> None:
    cues = (_cue(0.0, 10.0),)
    spans = parse_spans(((0.0, 3.0, 0),))

    pieces = split_cues_by_speaker(cues, spans)

    speakers = [piece.speaker for piece in pieces]
    assert 0 in speakers
    assert UNASSIGNED_SPEAKER in speakers


def test_overlapping_speech_is_left_unassigned_rather_than_guessed() -> None:
    cues = (_cue(0.0, 4.0),)
    spans = parse_spans(((0.0, 4.0, 0), (0.0, 4.0, 1)))

    pieces = split_cues_by_speaker(cues, spans)

    assert all(piece.speaker == UNASSIGNED_SPEAKER for piece in pieces)


def test_slivers_shorter_than_the_floor_are_dropped() -> None:
    cues = (_cue(0.0, 10.0),)
    spans = parse_spans(((0.0, 0.05, 0), (0.05, 10.0, 1)))

    pieces = split_cues_by_speaker(cues, spans)

    assert len(pieces) == 1
    assert pieces[0].speaker == 1


def test_speaker_shares_sum_to_one() -> None:
    cues = (_cue(0.0, 10.0),)
    spans = parse_spans(((0.0, 6.0, 0), (6.0, 10.0, 1)))

    stats = summarise_speakers(split_cues_by_speaker(cues, spans))

    assert sum(entry.share for entry in stats) == pytest.approx(1.0)
    assert {entry.speaker for entry in stats} == {0, 1}


def test_spans_are_validated_not_trusted() -> None:
    with pytest.raises(DiarizationError, match="positive duration"):
        parse_spans(((5.0, 5.0, 0),))
    with pytest.raises(DiarizationError, match="must not be negative"):
        parse_spans(((0.0, 1.0, -2),))
    with pytest.raises(DiarizationError, match="no speaker spans"):
        parse_spans(())


def test_summary_reports_how_many_pieces_came_from_split_cues() -> None:
    cues = (_cue(0.0, 10.0, "두 사람"), _cue(20.0, 24.0, "한 사람"))
    spans = parse_spans(((0.0, 4.0, 0), (4.0, 10.0, 1), (20.0, 24.0, 1)))

    summary = public_diarization_summary(split_cues_by_speaker(cues, spans), source_id="s")

    assert summary["pieces_from_split_cues"] == 2
    assert summary["piece_count"] == 3
    assert "두 사람" not in str(summary)


def test_labelling_refuses_an_empty_transcript() -> None:
    with pytest.raises(DiarizationError, match="no cues to label"):
        split_cues_by_speaker((), parse_spans(((0.0, 1.0, 0),)))


def test_sortformer_rows_are_parsed_into_spans() -> None:
    rows = [["0.00 4.12 speaker_0", "4.12 9.80 speaker_1"]]

    assert parse_prediction(rows) == ((0.0, 4.12, 0), (4.12, 9.8, 1))


def test_a_flat_row_list_is_accepted_too() -> None:
    assert parse_prediction(["1.0 2.0 speaker_2"]) == ((1.0, 2.0, 2),)


def test_an_unexpected_row_shape_fails_where_it_can_be_seen() -> None:
    with pytest.raises(DiarizationError, match="unexpected diarization row"):
        parse_prediction(["0.0 1.0"])
    with pytest.raises(DiarizationError, match="no segments"):
        parse_prediction([])
