"""Measure whether private Korean subtitles can serve as a raw transcript.

The project needs a ``raw_transcript`` that preserves repetition, hesitation and
ungrammatical speech (``docs/human-reference-design.md``). Human-authored
subtitles are normally edited in the opposite direction: fillers removed,
sentences tidied. This module measures that gap instead of assuming it.

Nothing here downloads media. Subtitle text stays in external storage; only
sanitized aggregates are safe to print.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Protocol

from companion.reference_storage import ReferenceStorage

SUBTITLE_PROBE_SCHEMA_VERSION = 1

# Human-authored track vs. the machine transcript of the original audio. The
# pair is what makes edit strength measurable; a single track cannot show it.
HUMAN_TRACK = "ko"
AUTOMATIC_TRACK = "ko-orig"

# Korean hesitation and hedge markers. Their survival rate is the clearest
# signal that a track kept spoken form rather than written form.
FILLER_TOKENS = (
    "어",
    "음",
    "그",
    "저기",
    "약간",
    "뭔가",
    "이제",
    "그니까",
    "그러니까",
    "아니",
    "진짜",
)

_SENTENCE_PUNCTUATION = ".?!…"
_CUE_TIMING = re.compile(
    r"^(?P<start>\d{1,3}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(?P<end>\d{1,3}:\d{2}:\d{2}[.,]\d{3})"
)
_INLINE_TAG = re.compile(r"<[^>]*>")
_WHITESPACE = re.compile(r"\s+")
_HANGUL_WORD = re.compile(r"[가-힣]+")


class ReferenceSubtitleProbeError(RuntimeError):
    """Raised when subtitle text cannot be measured without ambiguity."""


@dataclass(frozen=True)
class SubtitleCue:
    start_seconds: float
    end_seconds: float
    text: str

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class TrackMetrics:
    """Sanitized measurements. Deliberately holds no subtitle text."""

    track: str
    cue_count: int
    character_count: int
    covered_seconds: float
    coverage_ratio: float | None
    mean_cue_characters: float
    mean_cue_seconds: float
    filler_hits: int
    filler_per_1000_characters: float
    sentence_punctuation_count: int
    punctuation_per_1000_characters: float


@dataclass(frozen=True)
class TrackComparison:
    """How far the human track drifted from the machine transcript."""

    similarity_ratio: float
    character_ratio: float
    filler_retention_ratio: float | None


@dataclass(frozen=True)
class SourceSubtitleMetrics:
    source_id: str
    duration_seconds: float | None
    tracks: tuple[TrackMetrics, ...]
    comparison: TrackComparison | None
    missing_tracks: tuple[str, ...]
    # True when both track names resolved to the same machine transcript. The
    # source then has no human-authored Korean subtitle even though two files
    # were written, and no comparison is possible.
    identical_tracks: bool = False


@dataclass(frozen=True)
class SubtitleProbeFailure:
    source_id: str
    error: str


@dataclass(frozen=True)
class SubtitleProbeReport:
    schema_version: int
    generated_at: str
    candidate_id: str
    tool_version: str
    successes: tuple[SourceSubtitleMetrics, ...]
    failures: tuple[SubtitleProbeFailure, ...]


class SubtitleFetcher(Protocol):
    def version(self) -> str:
        ...

    def fetch(
        self,
        source_id: str,
        private_source_uri: str,
        destination: Path,
    ) -> dict[str, Path]:
        """Return {track name: subtitle file} for the requested Korean tracks."""
        ...


class YtDlpSubtitleFetcher:
    """Download only subtitle text with a pinned yt-dlp executable."""

    def __init__(
        self,
        executable: str = "yt-dlp",
        *,
        tracks: tuple[str, ...] = (HUMAN_TRACK, AUTOMATIC_TRACK),
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._executable = executable
        self._tracks = tracks
        self._run_command = run_command

    def version(self) -> str:
        completed = self._run([self._executable, "--version"], private_source_uri=None)
        version = completed.stdout.strip()
        if not version:
            raise ReferenceSubtitleProbeError("yt-dlp returned an empty version")
        return version

    def fetch(
        self,
        source_id: str,
        private_source_uri: str,
        destination: Path,
    ) -> dict[str, Path]:
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._run(
            [
                self._executable,
                "--ignore-config",
                "--no-cache-dir",
                "--no-playlist",
                "--no-warnings",
                # The contract of this probe: subtitle text and nothing else.
                "--skip-download",
                "--no-write-thumbnail",
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                ",".join(self._tracks),
                "--sub-format",
                "vtt",
                "--output",
                str(destination / f"{source_id}.%(ext)s"),
                private_source_uri,
            ],
            private_source_uri=private_source_uri,
        )
        found: dict[str, Path] = {}
        for track in self._tracks:
            candidate = destination / f"{source_id}.{track}.vtt"
            if candidate.is_file():
                candidate.chmod(0o600)
                found[track] = candidate
        _reject_media_files(destination)
        return found

    def _run(
        self,
        command: list[str],
        *,
        private_source_uri: str | None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["YTDLP_NO_PLUGINS"] = "1"
        completed = self._run_command(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "no diagnostic output"
            if private_source_uri:
                detail = detail.replace(private_source_uri, "<private-source-uri>")
            if len(detail) > 500:
                detail = f"{detail[:497]}..."
            raise ReferenceSubtitleProbeError(f"yt-dlp subtitle fetch failed: {detail}")
        return completed


def probe_candidate_subtitles(
    storage: ReferenceStorage,
    candidate_id: str,
    fetcher: SubtitleFetcher,
    *,
    generated_at: str,
    subtitle_relative_directory: str = "raw/subtitles",
    durations: dict[str, float] | None = None,
) -> SubtitleProbeReport:
    """Fetch and measure Korean subtitles for one approved candidate."""
    candidate = next(
        (
            entry
            for entry in storage.manifest.candidates
            if entry.candidate_id == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise ReferenceSubtitleProbeError("candidate ID is not registered in private manifest")
    if not candidate.sources:
        raise ReferenceSubtitleProbeError("candidate has no approved sources")

    base = storage.path(subtitle_relative_directory)
    if base.is_symlink() or not base.is_dir():
        raise ReferenceSubtitleProbeError("subtitle directory is missing or unsafe")

    successes: list[SourceSubtitleMetrics] = []
    failures: list[SubtitleProbeFailure] = []
    for source in candidate.sources:
        try:
            files = fetcher.fetch(
                source.source_id,
                source.private_source_uri,
                base / candidate_id,
            )
            metrics = measure_source_subtitles(
                source.source_id,
                {track: _read_text(path) for track, path in files.items()},
                duration_seconds=(durations or {}).get(source.source_id),
            )
        except ReferenceSubtitleProbeError as error:
            message = str(error).replace(source.private_source_uri, "<private-source-uri>")
            failures.append(
                SubtitleProbeFailure(source_id=source.source_id, error=message)
            )
        else:
            successes.append(metrics)

    return SubtitleProbeReport(
        schema_version=SUBTITLE_PROBE_SCHEMA_VERSION,
        generated_at=generated_at,
        candidate_id=candidate_id,
        tool_version=fetcher.version(),
        successes=tuple(successes),
        failures=tuple(failures),
    )


def measure_source_subtitles(
    source_id: str,
    track_documents: dict[str, str],
    *,
    duration_seconds: float | None = None,
) -> SourceSubtitleMetrics:
    """Measure every supplied track and compare the human and machine pair."""
    if duration_seconds is not None and (
        not math.isfinite(duration_seconds) or duration_seconds <= 0
    ):
        raise ReferenceSubtitleProbeError("source duration must be a positive number")

    parsed = {track: parse_webvtt(document) for track, document in track_documents.items()}
    metrics = tuple(
        track_metrics(track, cues, duration_seconds)
        for track, cues in sorted(parsed.items())
    )
    missing = tuple(
        track for track in (HUMAN_TRACK, AUTOMATIC_TRACK) if track not in parsed
    )
    comparison = None
    identical = False
    if HUMAN_TRACK in parsed and AUTOMATIC_TRACK in parsed:
        # yt-dlp happily writes both track names from automatic captions when a
        # source has no human subtitle. Comparing that file with itself scores a
        # perfect 1.0, which reads as "the subtitle is faithful" when it means
        # the opposite: there is nothing human to measure. Refuse the number.
        identical = _comparable(parsed[HUMAN_TRACK]) == _comparable(parsed[AUTOMATIC_TRACK])
        if not identical:
            comparison = compare_tracks(parsed[HUMAN_TRACK], parsed[AUTOMATIC_TRACK])
    return SourceSubtitleMetrics(
        source_id=source_id,
        duration_seconds=duration_seconds,
        tracks=metrics,
        comparison=comparison,
        missing_tracks=missing,
        identical_tracks=identical,
    )


def parse_webvtt(document: str) -> tuple[SubtitleCue, ...]:
    """Parse WebVTT into cues with rolling-caption duplication removed.

    YouTube automatic captions repeat the previous line and append new words to
    it. Counting those verbatim would inflate every text measurement several
    times over, so a cue that merely extends its predecessor keeps only the new
    part.
    """
    if not document.strip():
        raise ReferenceSubtitleProbeError("subtitle document is empty")
    if not document.lstrip("﻿").lstrip().startswith("WEBVTT"):
        raise ReferenceSubtitleProbeError("subtitle document is not WebVTT")

    cues: list[SubtitleCue] = []
    previous_text = ""
    lines = document.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        match = _CUE_TIMING.match(lines[index].strip())
        if match is None:
            index += 1
            continue
        start = _timestamp_seconds(match.group("start"))
        end = _timestamp_seconds(match.group("end"))
        if end < start:
            raise ReferenceSubtitleProbeError("subtitle cue ends before it starts")
        index += 1
        payload: list[str] = []
        while index < len(lines) and lines[index].strip():
            payload.append(lines[index])
            index += 1
        text = _clean_cue_text(" ".join(payload))
        incremental = _incremental_text(previous_text, text)
        if incremental:
            cues.append(SubtitleCue(start, end, incremental))
        if text:
            previous_text = text
    if not cues:
        raise ReferenceSubtitleProbeError("subtitle document has no usable cues")
    return tuple(cues)


def public_subtitle_summary(report: SubtitleProbeReport) -> dict[str, Any]:
    """Return an intentionally sanitized summary suitable for terminal output."""
    return {
        "schema_version": report.schema_version,
        "candidate_id": report.candidate_id,
        "tool_version": report.tool_version,
        "source_count": len(report.successes) + len(report.failures),
        "success_count": len(report.successes),
        "failure_count": len(report.failures),
        "sources": [
            {
                "source_id": source.source_id,
                "duration_seconds": source.duration_seconds,
                "missing_tracks": list(source.missing_tracks),
                "identical_tracks": source.identical_tracks,
                "tracks": [
                    {
                        "track": track.track,
                        "cue_count": track.cue_count,
                        "character_count": track.character_count,
                        "covered_seconds": round(track.covered_seconds, 1),
                        "coverage_ratio": _rounded(track.coverage_ratio, 3),
                        "mean_cue_characters": round(track.mean_cue_characters, 1),
                        "mean_cue_seconds": round(track.mean_cue_seconds, 2),
                        "filler_hits": track.filler_hits,
                        "filler_per_1000_characters": round(
                            track.filler_per_1000_characters, 2
                        ),
                        "sentence_punctuation_count": track.sentence_punctuation_count,
                        "punctuation_per_1000_characters": round(
                            track.punctuation_per_1000_characters, 2
                        ),
                    }
                    for track in source.tracks
                ],
                "comparison": None
                if source.comparison is None
                else {
                    "similarity_ratio": round(source.comparison.similarity_ratio, 3),
                    "character_ratio": round(source.comparison.character_ratio, 3),
                    "filler_retention_ratio": _rounded(
                        source.comparison.filler_retention_ratio, 3
                    ),
                },
            }
            for source in report.successes
        ],
        "failures": [
            {"source_id": failure.source_id, "error": failure.error}
            for failure in report.failures
        ],
    }


def dump_subtitle_probe_report(
    storage: ReferenceStorage,
    relative_path: str,
    report: SubtitleProbeReport,
) -> Path:
    """Create a mode-0600 report under reports/ without overwriting."""
    normalized = PurePosixPath(relative_path)
    if (
        len(normalized.parts) != 2
        or normalized.parts[0] != "reports"
        or normalized.suffix != ".json"
    ):
        raise ReferenceSubtitleProbeError(
            "subtitle probe report must be a direct reports/*.json path"
        )
    destination = storage.path(relative_path)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ReferenceSubtitleProbeError("private report directory is missing or unsafe")
    payload = public_subtitle_summary(report)
    payload["generated_at"] = report.generated_at
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
    except OSError as error:
        raise ReferenceSubtitleProbeError(
            "could not create private subtitle probe report"
        ) from error
    return destination


def track_metrics(
    track: str,
    cues: tuple[SubtitleCue, ...],
    duration_seconds: float | None,
) -> TrackMetrics:
    """Measure one parsed track. Public so other probes reuse the same maths."""
    text = " ".join(cue.text for cue in cues)
    characters = len(text.replace(" ", ""))
    covered = _merged_seconds(cues)
    fillers = _filler_hits(text)
    punctuation = sum(text.count(mark) for mark in _SENTENCE_PUNCTUATION)
    return TrackMetrics(
        track=track,
        cue_count=len(cues),
        character_count=characters,
        covered_seconds=covered,
        coverage_ratio=None if duration_seconds is None else covered / duration_seconds,
        mean_cue_characters=characters / len(cues),
        mean_cue_seconds=sum(cue.duration_seconds for cue in cues) / len(cues),
        filler_hits=fillers,
        filler_per_1000_characters=_per_thousand(fillers, characters),
        sentence_punctuation_count=punctuation,
        punctuation_per_1000_characters=_per_thousand(punctuation, characters),
    )


def compare_tracks(
    human: tuple[SubtitleCue, ...],
    automatic: tuple[SubtitleCue, ...],
) -> TrackComparison:
    """Compare two transcripts of the same audio. Public for reuse."""
    human_text = _comparable(human)
    automatic_text = _comparable(automatic)
    human_characters = len(human_text.replace(" ", ""))
    automatic_characters = len(automatic_text.replace(" ", ""))
    if not automatic_characters:
        raise ReferenceSubtitleProbeError("automatic track has no comparable text")
    human_fillers = _filler_hits(human_text)
    automatic_fillers = _filler_hits(automatic_text)
    return TrackComparison(
        similarity_ratio=SequenceMatcher(None, human_text, automatic_text).ratio(),
        character_ratio=human_characters / automatic_characters,
        filler_retention_ratio=(
            None if not automatic_fillers else human_fillers / automatic_fillers
        ),
    )


def _comparable(cues: tuple[SubtitleCue, ...]) -> str:
    """Normalize away formatting so the diff reflects wording, not layout."""
    joined = " ".join(cue.text for cue in cues)
    stripped = "".join(
        character for character in joined if character not in _SENTENCE_PUNCTUATION
    )
    return _WHITESPACE.sub(" ", stripped).strip().lower()


def _filler_hits(text: str) -> int:
    """Count filler markers as standalone words only.

    ``그`` inside ``그것`` is not hesitation. Matching on whole Hangul words
    keeps the measurement from drifting into ordinary vocabulary.
    """
    words = _HANGUL_WORD.findall(text)
    return sum(1 for word in words if word in FILLER_TOKENS)


def _merged_seconds(cues: tuple[SubtitleCue, ...]) -> float:
    """Sum cue time with overlaps counted once."""
    total = 0.0
    current_start: float | None = None
    current_end = 0.0
    for cue in sorted(cues, key=lambda item: (item.start_seconds, item.end_seconds)):
        if current_start is None:
            current_start, current_end = cue.start_seconds, cue.end_seconds
            continue
        if cue.start_seconds > current_end:
            total += current_end - current_start
            current_start, current_end = cue.start_seconds, cue.end_seconds
        else:
            current_end = max(current_end, cue.end_seconds)
    if current_start is not None:
        total += current_end - current_start
    return total


def _incremental_text(previous: str, current: str) -> str:
    """Return only the words this cue adds to its predecessor.

    Rolling captions repeat the *tail* of the previous cue at the head of the
    next one, not necessarily the whole of it. Matching on a whole-string prefix
    misses the partial overlaps and inflated every text measurement by roughly a
    third on real data, so the overlap is found token by token.

    Repetition inside a single cue survives untouched: only the boundary between
    two cues is deduplicated.
    """
    if not current:
        return ""
    previous_tokens = previous.split()
    current_tokens = current.split()
    overlap = _leading_overlap(previous_tokens, current_tokens)
    return " ".join(current_tokens[overlap:])


def _leading_overlap(previous: list[str], current: list[str]) -> int:
    """Length of the longest suffix of ``previous`` that starts ``current``."""
    for length in range(min(len(previous), len(current)), 0, -1):
        if previous[-length:] == current[:length]:
            return length
    return 0


def _clean_cue_text(value: str) -> str:
    return _WHITESPACE.sub(" ", _INLINE_TAG.sub("", value)).strip()


def _timestamp_seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _per_thousand(hits: int, characters: int) -> float:
    return 0.0 if not characters else hits * 1000 / characters


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReferenceSubtitleProbeError("could not read subtitle file") from error


def _reject_media_files(destination: Path) -> None:
    """Fail loudly if anything other than subtitle text appeared."""
    unexpected = [
        entry.name
        for entry in destination.iterdir()
        if entry.is_file() and entry.suffix.lower() not in {".vtt", ".srt"}
    ]
    if unexpected:
        raise ReferenceSubtitleProbeError(
            f"subtitle fetch unexpectedly produced {len(unexpected)} non-subtitle files"
        )
