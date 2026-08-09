"""Compare a local Whisper transcript against an independent ASR of the same audio.

There is no ground-truth transcript for a private source, so absolute accuracy
cannot be measured. What can be measured is agreement between two independent
systems: the local whisper.cpp run and the service-generated caption already
stored by the subtitle probe. Agreement is evidence; disagreement marks the
regions a human will have to check.

The second question is whether the local transcript keeps spoken form. A
transcript that tidies away hesitation is a ``normalized_transcript``, not the
``raw_transcript`` this project needs, no matter how accurate its words are.

Command construction lives here so it stays testable without Docker. Nothing in
this module prints transcript text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from companion.reference_subtitle_probe import (
    SubtitleCue,
    TrackComparison,
    TrackMetrics,
    compare_tracks,
    parse_webvtt,
    track_metrics,
)

TRANSCRIPTION_PROBE_SCHEMA_VERSION = 1

# whisper.cpp accepts 16 kHz mono PCM only. Anything else has to be converted
# first, which is why the segment step exists at all.
WHISPER_SAMPLE_RATE_HZ = 16000
WHISPER_CHANNELS = 1

LOCAL_TRACK = "whisper"
SERVICE_TRACK = "service"


class ReferenceTranscriptionProbeError(RuntimeError):
    """Raised when a transcription comparison cannot be made honestly."""


@dataclass(frozen=True)
class AudioSegment:
    start_seconds: float
    duration_seconds: float

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds


@dataclass(frozen=True)
class TranscriptionComparison:
    schema_version: int
    generated_at: str
    source_id: str
    segment: AudioSegment
    model_name: str
    tracks: tuple[TrackMetrics, ...]
    agreement: TrackComparison
    elapsed_seconds: float | None
    realtime_factor: float | None


def validate_segment(start_seconds: float, duration_seconds: float) -> AudioSegment:
    if start_seconds < 0:
        raise ReferenceTranscriptionProbeError("segment start must not be negative")
    if duration_seconds <= 0:
        raise ReferenceTranscriptionProbeError("segment duration must be positive")
    return AudioSegment(start_seconds=start_seconds, duration_seconds=duration_seconds)


def build_segment_command(
    *,
    input_path: Path,
    output_path: Path,
    segment: AudioSegment,
) -> list[str]:
    """Cut one segment and resample it for whisper.cpp.

    ``-ss`` precedes ``-i`` so ffmpeg seeks before decoding; on a 28 minute file
    that is the difference between a moment and a full decode pass.
    """
    if input_path == output_path:
        raise ReferenceTranscriptionProbeError("segment output must differ from its input")
    return [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{segment.start_seconds:.3f}",
        "-t",
        f"{segment.duration_seconds:.3f}",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        str(WHISPER_CHANNELS),
        "-ar",
        str(WHISPER_SAMPLE_RATE_HZ),
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]


def build_whisper_command(
    *,
    model_path: Path,
    audio_path: Path,
    output_prefix: Path,
    language: str = "ko",
    threads: int | None = None,
) -> list[str]:
    """Transcribe to WebVTT so the subtitle probe's parser and metrics apply.

    ``-ng`` keeps this an explicit CPU run: a silent GPU fallback would make the
    measured timing meaningless.
    """
    command = [
        "whisper-cli",
        "-m",
        str(model_path),
        "-f",
        str(audio_path),
        "-l",
        language,
        "-ovtt",
        "-of",
        str(output_prefix),
        "-np",
        "-ng",
    ]
    if threads is not None:
        if threads < 1:
            raise ReferenceTranscriptionProbeError("thread count must be at least 1")
        command.extend(["-t", str(threads)])
    return command


def slice_cues(
    cues: tuple[SubtitleCue, ...],
    segment: AudioSegment,
    *,
    rebase: bool = True,
) -> tuple[SubtitleCue, ...]:
    """Keep cues overlapping the segment, optionally rebased to segment time.

    Rebasing matters because the local transcript starts at zero while the
    service caption carries source-absolute timing. Comparing them without
    rebasing would compare different clocks.
    """
    selected: list[SubtitleCue] = []
    for cue in cues:
        if cue.end_seconds <= segment.start_seconds or cue.start_seconds >= segment.end_seconds:
            continue
        start = max(cue.start_seconds, segment.start_seconds)
        end = min(cue.end_seconds, segment.end_seconds)
        if rebase:
            start -= segment.start_seconds
            end -= segment.start_seconds
        selected.append(SubtitleCue(start_seconds=start, end_seconds=end, text=cue.text))
    if not selected:
        raise ReferenceTranscriptionProbeError(
            "reference transcript has no cues inside the requested segment"
        )
    return tuple(selected)


def compare_transcripts(
    source_id: str,
    segment: AudioSegment,
    local_vtt: str,
    service_vtt: str,
    *,
    generated_at: str,
    model_name: str,
    elapsed_seconds: float | None = None,
) -> TranscriptionComparison:
    """Measure both transcripts of one segment and their agreement."""
    local_cues = parse_webvtt(local_vtt)
    service_cues = slice_cues(parse_webvtt(service_vtt), segment)
    tracks = (
        track_metrics(LOCAL_TRACK, local_cues, segment.duration_seconds),
        track_metrics(SERVICE_TRACK, service_cues, segment.duration_seconds),
    )
    # Argument order sets the ratios' direction: local ÷ service.
    agreement = compare_tracks(local_cues, service_cues)
    realtime = None
    if elapsed_seconds is not None:
        if elapsed_seconds <= 0:
            raise ReferenceTranscriptionProbeError("elapsed time must be positive")
        realtime = segment.duration_seconds / elapsed_seconds
    return TranscriptionComparison(
        schema_version=TRANSCRIPTION_PROBE_SCHEMA_VERSION,
        generated_at=generated_at,
        source_id=source_id,
        segment=segment,
        model_name=model_name,
        tracks=tracks,
        agreement=agreement,
        elapsed_seconds=elapsed_seconds,
        realtime_factor=realtime,
    )


def public_transcription_summary(comparison: TranscriptionComparison) -> dict[str, Any]:
    """Return a summary safe to print; it contains no transcript text."""
    return {
        "schema_version": comparison.schema_version,
        "source_id": comparison.source_id,
        "model": comparison.model_name,
        "segment": {
            "start_seconds": comparison.segment.start_seconds,
            "duration_seconds": comparison.segment.duration_seconds,
        },
        "elapsed_seconds": comparison.elapsed_seconds,
        "realtime_factor": (
            None
            if comparison.realtime_factor is None
            else round(comparison.realtime_factor, 2)
        ),
        "tracks": [
            {
                "track": track.track,
                "cue_count": track.cue_count,
                "character_count": track.character_count,
                "covered_seconds": round(track.covered_seconds, 1),
                "coverage_ratio": (
                    None if track.coverage_ratio is None else round(track.coverage_ratio, 3)
                ),
                "mean_cue_characters": round(track.mean_cue_characters, 1),
                "mean_cue_seconds": round(track.mean_cue_seconds, 2),
                "filler_hits": track.filler_hits,
                "filler_per_1000_characters": round(track.filler_per_1000_characters, 2),
                "sentence_punctuation_count": track.sentence_punctuation_count,
                "punctuation_per_1000_characters": round(
                    track.punctuation_per_1000_characters, 2
                ),
            }
            for track in comparison.tracks
        ],
        "agreement": {
            "similarity_ratio": round(comparison.agreement.similarity_ratio, 3),
            "character_ratio": round(comparison.agreement.character_ratio, 3),
            "filler_retention_ratio": (
                None
                if comparison.agreement.filler_retention_ratio is None
                else round(comparison.agreement.filler_retention_ratio, 3)
            ),
        },
    }
