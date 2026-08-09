"""Offline coverage for the subtitle quality probe.

Every fixture here is synthetic Korean text written for this test. No real
subtitle content from a private source appears in the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from companion.reference_storage import (
    PrivateCandidateRef,
    PrivateSourceRef,
    ReferencePrivateManifest,
    ReferenceStorage,
    initialize_reference_storage,
    save_private_manifest,
)
from companion.reference_subtitle_probe import (
    AUTOMATIC_TRACK,
    HUMAN_TRACK,
    ReferenceSubtitleProbeError,
    SubtitleProbeReport,
    dump_subtitle_probe_report,
    measure_source_subtitles,
    parse_webvtt,
    probe_candidate_subtitles,
    public_subtitle_summary,
)

_EDITED = """WEBVTT

00:00:01.000 --> 00:00:04.000
오늘은 연습을 했습니다.

00:00:05.000 --> 00:00:08.000
정말 즐거운 하루였습니다.
"""

_SPOKEN = """WEBVTT

00:00:01.000 --> 00:00:04.000
어 그 오늘은 약간 뭔가 연습을 했어요

00:00:05.000 --> 00:00:08.000
음 진짜 즐거운 하루였어요
"""

_ROLLING = """WEBVTT

00:00:01.000 --> 00:00:02.000
<00:00:01.500><c>안녕</c>

00:00:02.000 --> 00:00:03.000
안녕 하세요

00:00:03.000 --> 00:00:04.000
안녕 하세요 여러분
"""


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    repository_root = tmp_path / "repo"
    external_root = tmp_path / "external"
    repository_root.mkdir()
    external_root.mkdir()
    return repository_root, external_root


def _storage_with_sources(tmp_path: Path, source_ids: tuple[str, ...]) -> ReferenceStorage:
    repository_root, external_root = _roots(tmp_path)
    storage = initialize_reference_storage(external_root, repository_root)
    manifest = ReferencePrivateManifest(
        schema_version=storage.manifest.schema_version,
        storage_id=storage.storage_id,
        candidates=(
            PrivateCandidateRef(
                candidate_id="candidate-001",
                private_identity_ref="private/identity",
                sources=tuple(
                    PrivateSourceRef(
                        source_id=source_id,
                        private_source_uri=f"https://example.invalid/{source_id}",
                    )
                    for source_id in source_ids
                ),
            ),
        ),
    )
    return save_private_manifest(storage, manifest)


class _FakeFetcher:
    """Writes synthetic subtitle files instead of contacting any service."""

    def __init__(self, documents: dict[str, dict[str, str]]) -> None:
        self._documents = documents
        self.requested_uris: list[str] = []

    def version(self) -> str:
        return "fake-1"

    def fetch(
        self,
        source_id: str,
        private_source_uri: str,
        destination: Path,
    ) -> dict[str, Path]:
        self.requested_uris.append(private_source_uri)
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        written: dict[str, Path] = {}
        for track, document in self._documents.get(source_id, {}).items():
            path = destination / f"{source_id}.{track}.vtt"
            path.write_text(document, encoding="utf-8")
            written[track] = path
        return written


class _FailingFetcher(_FakeFetcher):
    def fetch(
        self,
        source_id: str,
        private_source_uri: str,
        destination: Path,
    ) -> dict[str, Path]:
        raise ReferenceSubtitleProbeError(f"fetch failed for {private_source_uri}")


def test_parse_webvtt_reads_timing_and_text() -> None:
    cues = parse_webvtt(_EDITED)

    assert len(cues) == 2
    assert cues[0].start_seconds == 1.0
    assert cues[0].end_seconds == 4.0
    assert cues[0].text == "오늘은 연습을 했습니다."


def test_parse_webvtt_keeps_only_new_words_of_rolling_captions() -> None:
    cues = parse_webvtt(_ROLLING)

    # Naive parsing would count "안녕" three times and "하세요" twice.
    assert [cue.text for cue in cues] == ["안녕", "하세요", "여러분"]


def test_parse_webvtt_strips_inline_timing_tags() -> None:
    assert parse_webvtt(_ROLLING)[0].text == "안녕"


def test_parse_webvtt_rejects_documents_that_are_not_webvtt() -> None:
    with pytest.raises(ReferenceSubtitleProbeError, match="not WebVTT"):
        parse_webvtt("1\n00:00:01,000 --> 00:00:02,000\n안녕\n")


def test_parse_webvtt_rejects_empty_and_cueless_documents() -> None:
    with pytest.raises(ReferenceSubtitleProbeError, match="empty"):
        parse_webvtt("   ")
    with pytest.raises(ReferenceSubtitleProbeError, match="no usable cues"):
        parse_webvtt("WEBVTT\n\nNOTE nothing here\n")


def test_parse_webvtt_rejects_reversed_cue_timing() -> None:
    document = "WEBVTT\n\n00:00:05.000 --> 00:00:01.000\n안녕\n"

    with pytest.raises(ReferenceSubtitleProbeError, match="ends before it starts"):
        parse_webvtt(document)


def test_filler_count_ignores_markers_inside_ordinary_words() -> None:
    # "그것" and "어제" contain filler characters but are not hesitation.
    document = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n그것은 어제 일이다\n"

    metrics = measure_source_subtitles("source-001", {HUMAN_TRACK: document})

    assert metrics.tracks[0].filler_hits == 0


def test_edited_track_scores_lower_filler_rate_than_spoken_track() -> None:
    metrics = measure_source_subtitles(
        "source-001",
        {HUMAN_TRACK: _EDITED, AUTOMATIC_TRACK: _SPOKEN},
    )

    by_track = {track.track: track for track in metrics.tracks}
    assert by_track[HUMAN_TRACK].filler_hits == 0
    assert by_track[AUTOMATIC_TRACK].filler_hits > 0
    assert by_track[HUMAN_TRACK].sentence_punctuation_count > 0
    assert by_track[AUTOMATIC_TRACK].sentence_punctuation_count == 0


def test_comparison_reports_editing_strength_between_the_two_tracks() -> None:
    metrics = measure_source_subtitles(
        "source-001",
        {HUMAN_TRACK: _EDITED, AUTOMATIC_TRACK: _SPOKEN},
    )

    assert metrics.comparison is not None
    assert metrics.comparison.similarity_ratio < 1.0
    # The edited track drops fillers, so it is shorter than the spoken one.
    assert metrics.comparison.character_ratio < 1.0
    assert metrics.comparison.filler_retention_ratio == 0.0
    assert metrics.missing_tracks == ()


def test_identical_tracks_are_flagged_and_never_scored_as_a_faithful_subtitle() -> None:
    # A source without a human subtitle still yields two files: yt-dlp writes the
    # automatic caption under both track names. Scoring that pair would report a
    # perfect 1.0 similarity and invert the conclusion.
    metrics = measure_source_subtitles(
        "source-001",
        {HUMAN_TRACK: _SPOKEN, AUTOMATIC_TRACK: _SPOKEN},
    )

    assert metrics.identical_tracks is True
    assert metrics.comparison is None


def test_distinct_tracks_are_not_flagged_as_identical() -> None:
    metrics = measure_source_subtitles(
        "source-001",
        {HUMAN_TRACK: _EDITED, AUTOMATIC_TRACK: _SPOKEN},
    )

    assert metrics.identical_tracks is False
    assert metrics.comparison is not None


def test_identical_tracks_ignore_punctuation_and_spacing_differences() -> None:
    # Same wording, different formatting is still the same transcript.
    respaced = _SPOKEN.replace("어 그 오늘은", "어  그  오늘은.")

    metrics = measure_source_subtitles(
        "source-001",
        {HUMAN_TRACK: _SPOKEN, AUTOMATIC_TRACK: respaced},
    )

    assert metrics.identical_tracks is True


def test_missing_track_is_reported_without_a_comparison() -> None:
    metrics = measure_source_subtitles("source-001", {AUTOMATIC_TRACK: _SPOKEN})

    assert metrics.comparison is None
    assert metrics.missing_tracks == (HUMAN_TRACK,)


def test_coverage_ratio_uses_known_duration_and_merges_overlaps() -> None:
    overlapping = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:06.000\n안녕\n\n"
        "00:00:04.000 --> 00:00:10.000\n반가워\n"
    )

    metrics = measure_source_subtitles(
        "source-001", {HUMAN_TRACK: overlapping}, duration_seconds=20.0
    )

    # Overlapping cues cover 10 seconds, not 12.
    assert metrics.tracks[0].covered_seconds == 10.0
    assert metrics.tracks[0].coverage_ratio == 0.5


def test_coverage_ratio_is_absent_without_a_known_duration() -> None:
    metrics = measure_source_subtitles("source-001", {HUMAN_TRACK: _EDITED})

    assert metrics.tracks[0].coverage_ratio is None


def test_measure_rejects_a_non_positive_duration() -> None:
    with pytest.raises(ReferenceSubtitleProbeError, match="positive number"):
        measure_source_subtitles("source-001", {HUMAN_TRACK: _EDITED}, duration_seconds=0.0)


def test_probe_measures_every_registered_source(tmp_path: Path) -> None:
    storage = _storage_with_sources(tmp_path, ("source-001", "source-002"))
    fetcher = _FakeFetcher(
        {
            "source-001": {AUTOMATIC_TRACK: _SPOKEN},
            "source-002": {HUMAN_TRACK: _EDITED, AUTOMATIC_TRACK: _SPOKEN},
        }
    )

    report = probe_candidate_subtitles(
        storage,
        "candidate-001",
        fetcher,
        generated_at="2026-08-09T00:00:00+00:00",
        durations={"source-001": 100.0},
    )

    assert report.failures == ()
    assert [source.source_id for source in report.successes] == [
        "source-001",
        "source-002",
    ]
    assert report.successes[0].missing_tracks == (HUMAN_TRACK,)
    assert report.successes[1].comparison is not None


def test_probe_records_failures_without_leaking_the_private_uri(tmp_path: Path) -> None:
    storage = _storage_with_sources(tmp_path, ("source-001",))

    report = probe_candidate_subtitles(
        storage,
        "candidate-001",
        _FailingFetcher({}),
        generated_at="2026-08-09T00:00:00+00:00",
    )

    assert report.successes == ()
    assert len(report.failures) == 1
    assert "<private-source-uri>" in report.failures[0].error
    assert "example.invalid" not in report.failures[0].error


def test_probe_rejects_an_unregistered_candidate(tmp_path: Path) -> None:
    storage = _storage_with_sources(tmp_path, ("source-001",))

    with pytest.raises(ReferenceSubtitleProbeError, match="not registered"):
        probe_candidate_subtitles(
            storage,
            "candidate-999",
            _FakeFetcher({}),
            generated_at="2026-08-09T00:00:00+00:00",
        )


def test_public_summary_excludes_subtitle_text(tmp_path: Path) -> None:
    storage = _storage_with_sources(tmp_path, ("source-001",))
    report = probe_candidate_subtitles(
        storage,
        "candidate-001",
        _FakeFetcher({"source-001": {HUMAN_TRACK: _EDITED, AUTOMATIC_TRACK: _SPOKEN}}),
        generated_at="2026-08-09T00:00:00+00:00",
    )

    serialized = str(public_subtitle_summary(report))

    assert "연습을" not in serialized
    assert "즐거운" not in serialized
    assert "example.invalid" not in serialized


def test_report_is_written_once_with_owner_only_permissions(tmp_path: Path) -> None:
    storage = _storage_with_sources(tmp_path, ("source-001",))
    report = probe_candidate_subtitles(
        storage,
        "candidate-001",
        _FakeFetcher({"source-001": {AUTOMATIC_TRACK: _SPOKEN}}),
        generated_at="2026-08-09T00:00:00+00:00",
    )

    written = dump_subtitle_probe_report(storage, "reports/subtitles.json", report)

    assert written.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ReferenceSubtitleProbeError, match="could not create"):
        dump_subtitle_probe_report(storage, "reports/subtitles.json", report)


def test_report_path_must_stay_directly_under_reports(tmp_path: Path) -> None:
    storage = _storage_with_sources(tmp_path, ("source-001",))
    report = SubtitleProbeReport(
        schema_version=1,
        generated_at="2026-08-09T00:00:00+00:00",
        candidate_id="candidate-001",
        tool_version="fake-1",
        successes=(),
        failures=(),
    )

    for bad_path in ("raw/subtitles.json", "reports/nested/subtitles.json", "reports/x.txt"):
        with pytest.raises(ReferenceSubtitleProbeError, match="reports/\\*.json"):
            dump_subtitle_probe_report(storage, bad_path, report)
