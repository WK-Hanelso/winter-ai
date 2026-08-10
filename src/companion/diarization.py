"""Attach speaker labels to transcript cues using a diarization timeline.

The blocking problem was never transcription quality: it was that one transcript
cue routinely holds a question and its answer. Splitting the cue at the point
where the speaker changes is what turns an interview into (prompt, response)
pairs.

A diarizer supplies ``(start, end, speaker)`` spans. This module intersects them
with the cue timings that were already produced and verified, so the transcript
does not have to be regenerated to gain speaker labels.

Two rules shape the result:

* a cue crossing a speaker change is split, not assigned to whichever speaker
  happens to cover more of it;
* a piece nobody speaks over is kept and marked unassigned rather than dropped,
  because silently discarding audio hides how much of a source is unusable.

No model, no audio. The diarizer is an adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from companion.reference_subtitle_probe import SubtitleCue

DIARIZATION_SCHEMA_VERSION = 1

# Splits shorter than this are transcription seams, not turns.
MINIMUM_PIECE_SECONDS = 0.2

UNASSIGNED_SPEAKER = -1


class DiarizationError(RuntimeError):
    """Raised when a speaker timeline cannot be applied without guessing."""


@dataclass(frozen=True)
class SpeakerSpan:
    start_seconds: float
    end_seconds: float
    speaker: int

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class SpeakerCue:
    """One cue, or a piece of one, credited to a single speaker."""

    start_seconds: float
    end_seconds: float
    text: str
    speaker: int
    source_cue_index: int
    was_split: bool

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class SpeakerStats:
    speaker: int
    piece_count: int
    total_seconds: float
    share: float


def parse_prediction(rows: object) -> tuple[tuple[float, float, int], ...]:
    """Read Sortformer's ``begin, end, speaker`` output.

    The model returns one list per input file; the rows inside are strings such
    as ``"12.34 15.67 speaker_1"``. Parsing is kept separate from the model call
    so a format change surfaces here rather than deep inside the run.
    """
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        rows = rows[0]
    if not isinstance(rows, list) or not rows:
        raise DiarizationError("diarization returned no segments")
    parsed: list[tuple[float, float, int]] = []
    for row in rows:
        fields = str(row).replace(",", " ").split()
        if len(fields) < 3:
            raise DiarizationError(f"unexpected diarization row with {len(fields)} fields")
        start, end = float(fields[0]), float(fields[1])
        speaker = int("".join(character for character in fields[2] if character.isdigit()))
        parsed.append((start, end, speaker))
    return tuple(parsed)


def parse_spans(rows: tuple[tuple[float, float, int], ...]) -> tuple[SpeakerSpan, ...]:
    spans: list[SpeakerSpan] = []
    for start, end, speaker in rows:
        if end <= start:
            raise DiarizationError("speaker span must have positive duration")
        if speaker < 0:
            raise DiarizationError("speaker index must not be negative")
        spans.append(SpeakerSpan(start_seconds=start, end_seconds=end, speaker=speaker))
    if not spans:
        raise DiarizationError("diarization produced no speaker spans")
    return tuple(sorted(spans, key=lambda span: (span.start_seconds, span.speaker)))


def split_cues_by_speaker(
    cues: tuple[SubtitleCue, ...],
    spans: tuple[SpeakerSpan, ...],
    *,
    minimum_piece_seconds: float = MINIMUM_PIECE_SECONDS,
) -> tuple[SpeakerCue, ...]:
    """Cut every cue at the speaker changes that fall inside it.

    Text is not divided: a cue split in two keeps its whole text on both pieces,
    marked ``was_split``. Guessing which words belong to which speaker from
    timing alone would invent an alignment the transcript does not contain.
    Callers decide whether a split cue's text is usable.
    """
    if not cues:
        raise DiarizationError("no cues to label")
    pieces: list[SpeakerCue] = []
    for index, cue in enumerate(cues):
        boundaries = _boundaries_within(cue, spans)
        was_split = len(boundaries) > 2
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            if end - start < minimum_piece_seconds:
                continue
            pieces.append(
                SpeakerCue(
                    start_seconds=start,
                    end_seconds=end,
                    text=cue.text,
                    speaker=_speaker_at(start, end, spans),
                    source_cue_index=index,
                    was_split=was_split,
                )
            )
    if not pieces:
        raise DiarizationError("labelling produced no usable pieces")
    return tuple(pieces)


def summarise_speakers(pieces: tuple[SpeakerCue, ...]) -> tuple[SpeakerStats, ...]:
    if not pieces:
        raise DiarizationError("no pieces to summarise")
    totals: dict[int, list[float]] = {}
    for piece in pieces:
        entry = totals.setdefault(piece.speaker, [0.0, 0.0])
        entry[0] += 1
        entry[1] += piece.duration_seconds
    overall = sum(entry[1] for entry in totals.values())
    return tuple(
        SpeakerStats(
            speaker=speaker,
            piece_count=int(entry[0]),
            total_seconds=entry[1],
            share=entry[1] / overall if overall else 0.0,
        )
        for speaker, entry in sorted(totals.items())
    )


def public_diarization_summary(
    pieces: tuple[SpeakerCue, ...],
    *,
    source_id: str,
) -> dict[str, Any]:
    """Counts and durations only. Cue text never appears here."""
    stats = summarise_speakers(pieces)
    split_pieces = sum(1 for piece in pieces if piece.was_split)
    return {
        "schema_version": DIARIZATION_SCHEMA_VERSION,
        "source_id": source_id,
        "piece_count": len(pieces),
        # How many pieces came from a cue that held more than one speaker. This
        # is the measurement that motivated the whole step.
        "pieces_from_split_cues": split_pieces,
        "split_share": round(split_pieces / len(pieces), 4),
        "unassigned_pieces": sum(
            1 for piece in pieces if piece.speaker == UNASSIGNED_SPEAKER
        ),
        "speakers": [
            {
                "speaker": entry.speaker,
                "piece_count": entry.piece_count,
                "total_seconds": round(entry.total_seconds, 2),
                "share": round(entry.share, 4),
            }
            for entry in stats
        ],
    }


def _boundaries_within(
    cue: SubtitleCue,
    spans: tuple[SpeakerSpan, ...],
) -> tuple[float, ...]:
    points = {cue.start_seconds, cue.end_seconds}
    for span in spans:
        for edge in (span.start_seconds, span.end_seconds):
            if cue.start_seconds < edge < cue.end_seconds:
                points.add(edge)
    return tuple(sorted(points))


def _speaker_at(start: float, end: float, spans: tuple[SpeakerSpan, ...]) -> int:
    """Credit the piece to whichever speaker covers most of it.

    Ties and gaps go to ``UNASSIGNED_SPEAKER``. Overlapping speech lands here
    too when no single speaker dominates, which is the honest answer: the
    transcript holds one line and two people said it.
    """
    best_speaker = UNASSIGNED_SPEAKER
    best_overlap = 0.0
    tied = False
    for span in spans:
        overlap = min(end, span.end_seconds) - max(start, span.start_seconds)
        if overlap <= 0:
            continue
        if overlap > best_overlap + 1e-9:
            best_speaker, best_overlap, tied = span.speaker, overlap, False
        elif abs(overlap - best_overlap) <= 1e-9 and span.speaker != best_speaker:
            tied = True
    if tied or best_overlap <= 0:
        return UNASSIGNED_SPEAKER
    return best_speaker
