from pathlib import Path
import wave

from experiments.reference_response_clips_48k import (
    CHUNK_RATE,
    CLIP_NAME,
    TARGET_RATE,
    SourceLocator,
    Span,
    correlation,
    cut,
    read_wave,
    write_wave,
)
import numpy as np
import pytest


def tone(seconds: float, rate: int, frequency: float = 220.0) -> np.ndarray:
    time = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * frequency * time) * 8000).astype(np.int16)


def noise(seconds: float, rate: int, seed: int) -> np.ndarray:
    generator = np.random.default_rng(seed)
    return generator.integers(-8000, 8000, int(seconds * rate), dtype=np.int16)


def test_clip_names_yield_source_and_chunk() -> None:
    matched = CLIP_NAME.match("source-003-600-300-response-007")

    assert matched is not None
    assert matched["source"] == "source-003"
    assert matched["offset"] == "600"
    assert int(matched["index"]) == 7


def test_a_name_without_a_response_index_is_rejected() -> None:
    assert CLIP_NAME.match("source-003-600-300") is None


def test_identical_signals_correlate_at_one() -> None:
    signal = noise(0.5, CHUNK_RATE, seed=1)

    assert correlation(signal, signal) == pytest.approx(1.0)


def test_unrelated_signals_correlate_near_zero() -> None:
    assert abs(correlation(noise(0.5, CHUNK_RATE, 1), noise(0.5, CHUNK_RATE, 2))) < 0.2


def test_a_clip_is_found_where_it_was_taken_from() -> None:
    recording = noise(20.0, CHUNK_RATE, seed=7)
    start = int(6.25 * CHUNK_RATE)
    clip = recording[start : start + int(2.0 * CHUNK_RATE)]

    seconds, confidence = SourceLocator(recording, len(clip)).locate(clip)

    assert seconds == pytest.approx(6.25, abs=1 / CHUNK_RATE)
    assert confidence == pytest.approx(1.0)


def test_a_clip_from_elsewhere_is_reported_as_not_found() -> None:
    # The caller drops anything below its threshold, so a wrong peak has to come
    # back with a low correlation rather than a plausible position.
    recording = noise(20.0, CHUNK_RATE, seed=7)
    _, confidence = SourceLocator(recording, CHUNK_RATE).locate(noise(1.0, CHUNK_RATE, seed=99))

    assert confidence < 0.5


def test_the_cut_takes_the_span_the_position_names(tmp_path: Path) -> None:
    full = np.concatenate([
        tone(1.0, TARGET_RATE, 200.0),
        tone(0.5, TARGET_RATE, 900.0),
        tone(1.0, TARGET_RATE, 200.0),
    ])
    span = Span(
        name="clip",
        source="source-003",
        start_seconds=1.0,
        duration_seconds=0.5,
        confidence=1.0,
    )

    written = cut(full, span, tmp_path)
    middle = full[TARGET_RATE : TARGET_RATE + TARGET_RATE // 2]

    assert correlation(read_wave(written), middle) == pytest.approx(1.0)


def test_written_clips_are_not_world_readable(tmp_path: Path) -> None:
    # Reference media. The storage root is mode 700 by contract and copies of it
    # should not relax that.
    path = tmp_path / "clip.wav"
    write_wave(path, tone(0.1, TARGET_RATE), TARGET_RATE)

    assert path.stat().st_mode & 0o077 == 0


def test_written_clips_carry_the_rate_they_were_written_at(tmp_path: Path) -> None:
    path = tmp_path / "clip.wav"
    write_wave(path, tone(0.25, TARGET_RATE), TARGET_RATE)

    with wave.open(str(path)) as handle:
        assert handle.getframerate() == TARGET_RATE
        assert handle.getnchannels() == 1
        assert handle.getnframes() == TARGET_RATE // 4
