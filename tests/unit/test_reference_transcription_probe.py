"""Offline coverage for the STT pilot comparison.

No Docker, no model, no real transcript. Fixtures are synthetic Korean written
for this test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from companion.reference_subtitle_probe import SubtitleCue, parse_webvtt
from companion.reference_transcription_probe import (
    LOCAL_TRACK,
    SERVICE_TRACK,
    WHISPER_SAMPLE_RATE_HZ,
    AudioSegment,
    ReferenceTranscriptionProbeError,
    build_segment_command,
    build_whisper_command,
    compare_transcripts,
    public_transcription_summary,
    slice_cues,
    validate_segment,
)

_LOCAL = """WEBVTT

00:00:00.000 --> 00:00:04.000
어 그 오늘은 약간 연습을 했어요

00:00:04.000 --> 00:00:08.000
음 진짜 즐거웠어요
"""

_SERVICE = """WEBVTT

00:10:00.000 --> 00:10:04.000
오늘은 연습을 했어요

00:10:04.000 --> 00:10:08.000
즐거웠어요

00:20:00.000 --> 00:20:04.000
이건 구간 밖이다
"""


def test_validate_segment_rejects_impossible_windows() -> None:
    with pytest.raises(ReferenceTranscriptionProbeError, match="must not be negative"):
        validate_segment(-1.0, 10.0)
    with pytest.raises(ReferenceTranscriptionProbeError, match="must be positive"):
        validate_segment(0.0, 0.0)


def test_segment_command_seeks_before_decoding_and_targets_whisper_input() -> None:
    command = build_segment_command(
        input_path=Path("/work/a.webm"),
        output_path=Path("/work/a.wav"),
        segment=AudioSegment(600.0, 300.0),
    )

    # -ss must precede -i, otherwise ffmpeg decodes the whole file first.
    assert command.index("-ss") < command.index("-i")
    assert "-ar" in command and command[command.index("-ar") + 1] == str(
        WHISPER_SAMPLE_RATE_HZ
    )
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-c:a") + 1] == "pcm_s16le"
    assert "-vn" in command


def test_segment_command_refuses_to_overwrite_its_own_input() -> None:
    with pytest.raises(ReferenceTranscriptionProbeError, match="must differ"):
        build_segment_command(
            input_path=Path("/work/a.wav"),
            output_path=Path("/work/a.wav"),
            segment=AudioSegment(0.0, 1.0),
        )


def test_whisper_command_requests_vtt_and_pins_cpu_execution() -> None:
    command = build_whisper_command(
        model_path=Path("/models/ggml-small.bin"),
        audio_path=Path("/work/a.wav"),
        output_prefix=Path("/work/a"),
        language="ko",
    )

    assert "-ovtt" in command
    # -ng keeps it an explicit CPU run; a silent GPU fallback would break timing.
    assert "-ng" in command
    assert command[command.index("-l") + 1] == "ko"


def test_whisper_command_rejects_a_meaningless_thread_count() -> None:
    with pytest.raises(ReferenceTranscriptionProbeError, match="at least 1"):
        build_whisper_command(
            model_path=Path("/models/m.bin"),
            audio_path=Path("/work/a.wav"),
            output_prefix=Path("/work/a"),
            threads=0,
        )


def test_slice_cues_keeps_only_the_window_and_rebases_to_segment_time() -> None:
    cues = parse_webvtt(_SERVICE)

    sliced = slice_cues(cues, AudioSegment(600.0, 300.0))

    assert len(sliced) == 2
    # 00:10:00 absolute becomes 0.0 relative to a segment starting at 600 s.
    assert sliced[0].start_seconds == 0.0
    assert sliced[1].end_seconds == 8.0


def test_slice_cues_can_keep_absolute_timing() -> None:
    sliced = slice_cues(parse_webvtt(_SERVICE), AudioSegment(600.0, 300.0), rebase=False)

    assert sliced[0].start_seconds == 600.0


def test_slice_cues_clips_a_cue_that_straddles_the_boundary() -> None:
    cues = (SubtitleCue(590.0, 610.0, "걸친 구간"),)

    sliced = slice_cues(cues, AudioSegment(600.0, 300.0))

    assert sliced[0].start_seconds == 0.0
    assert sliced[0].end_seconds == 10.0


def test_slice_cues_fails_when_the_window_holds_no_reference() -> None:
    with pytest.raises(ReferenceTranscriptionProbeError, match="no cues inside"):
        slice_cues(parse_webvtt(_SERVICE), AudioSegment(3600.0, 60.0))


def test_comparison_measures_both_tracks_and_their_agreement() -> None:
    comparison = compare_transcripts(
        "source-003",
        AudioSegment(600.0, 300.0),
        _LOCAL,
        _SERVICE,
        generated_at="2026-08-09T00:00:00+00:00",
        model_name="ggml-small.bin",
    )

    tracks = {track.track: track for track in comparison.tracks}
    assert set(tracks) == {LOCAL_TRACK, SERVICE_TRACK}
    # The local transcript here keeps fillers; the service one dropped them.
    assert tracks[LOCAL_TRACK].filler_hits > tracks[SERVICE_TRACK].filler_hits
    assert comparison.agreement.similarity_ratio < 1.0
    assert comparison.agreement.character_ratio > 1.0


def test_realtime_factor_reports_speed_against_segment_length() -> None:
    comparison = compare_transcripts(
        "source-003",
        AudioSegment(0.0, 300.0),
        _LOCAL,
        _SERVICE.replace("00:10:0", "00:00:0").replace("00:20:0", "00:04:0"),
        generated_at="2026-08-09T00:00:00+00:00",
        model_name="ggml-small.bin",
        elapsed_seconds=150.0,
    )

    assert comparison.realtime_factor == 2.0


def test_comparison_rejects_a_non_positive_elapsed_time() -> None:
    with pytest.raises(ReferenceTranscriptionProbeError, match="must be positive"):
        compare_transcripts(
            "source-003",
            AudioSegment(600.0, 300.0),
            _LOCAL,
            _SERVICE,
            generated_at="2026-08-09T00:00:00+00:00",
            model_name="ggml-small.bin",
            elapsed_seconds=0.0,
        )


def test_public_summary_excludes_transcript_text() -> None:
    comparison = compare_transcripts(
        "source-003",
        AudioSegment(600.0, 300.0),
        _LOCAL,
        _SERVICE,
        generated_at="2026-08-09T00:00:00+00:00",
        model_name="ggml-small.bin",
    )

    serialized = str(public_transcription_summary(comparison))

    assert "연습을" not in serialized
    assert "즐거" not in serialized
    assert "source-003" in serialized


def test_docker_command_maps_the_host_user_so_outputs_are_not_root_owned() -> None:
    from experiments.reference_transcription_probe import build_docker_command

    command = build_docker_command(
        image="example/image",
        model_dir=Path("/models"),
        work_dir=Path("/work"),
        inner_command=["whisper-cli", "-m", "/models/m.bin"],
        user="1000:1000",
    )

    assert command[command.index("--user") + 1] == "1000:1000"
    assert command[command.index("--entrypoint") + 1] == "whisper-cli"
    assert "/models:/models:ro" in command


def test_docker_command_omits_user_mapping_when_not_requested() -> None:
    from experiments.reference_transcription_probe import build_docker_command

    command = build_docker_command(
        image="example/image",
        model_dir=Path("/models"),
        work_dir=Path("/work"),
        inner_command=["ffmpeg", "-i", "x"],
    )

    assert "--user" not in command


def test_word_timestamp_mode_asks_whisper_for_one_word_per_cue() -> None:
    command = build_whisper_command(
        model_path=Path("/models/m.bin"),
        audio_path=Path("/work/a.wav"),
        output_prefix=Path("/work/a"),
        word_timestamps=True,
    )

    # Speaker boundaries fall between words, so a sentence-sized cue can never
    # be credited to one speaker.
    assert command[command.index("-ml") + 1] == "1"
    assert "-sow" in command


def test_word_timestamps_are_off_unless_requested() -> None:
    command = build_whisper_command(
        model_path=Path("/models/m.bin"),
        audio_path=Path("/work/a.wav"),
        output_prefix=Path("/work/a"),
    )

    assert "-ml" not in command
