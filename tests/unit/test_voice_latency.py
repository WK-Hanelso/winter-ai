from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from companion.voice_latency import (
    SqliteVoiceLatencyRepository,
    VoiceLatencyError,
    VoiceLatencySample,
)


def _sample(
    index: int,
    *,
    release_to_play_ms: float | None = None,
) -> VoiceLatencySample:
    play = release_to_play_ms if release_to_play_ms is not None else 4000 + index
    return VoiceLatencySample(
        trace_id=f"trace-{index}",
        release_to_stt_ms=1000,
        release_to_first_sentence_ms=2000,
        release_to_first_audio_ms=3000,
        release_to_play_ms=play,
        server_stt_ms=900,
        server_first_sentence_ms=800,
        server_first_audio_ms=1800,
        created_at=(datetime(2026, 8, 17, tzinfo=UTC) + timedelta(seconds=index)).isoformat(),
    )


def test_sample_rejects_non_monotonic_client_milestones() -> None:
    with pytest.raises(VoiceLatencyError, match="monotonic"):
        VoiceLatencySample.from_payload(
            {
                "trace_id": "bad-order",
                "release_to_stt_ms": 1000,
                "release_to_first_sentence_ms": 900,
                "release_to_first_audio_ms": 2000,
                "release_to_play_ms": 2100,
                "server_stt_ms": 800,
                "server_first_sentence_ms": 700,
                "server_first_audio_ms": 1700,
            }
        )


def test_repository_persists_once_per_trace_id(tmp_path: Path) -> None:
    path = tmp_path / "voice-latency.sqlite"
    repository = SqliteVoiceLatencyRepository(path)
    sample = _sample(1)

    assert repository.record(sample) is True
    assert repository.record(sample) is False
    assert SqliteVoiceLatencyRepository(path).list_recent() == (sample,)


def test_summary_requires_twenty_turns_and_uses_recent_window(tmp_path: Path) -> None:
    repository = SqliteVoiceLatencyRepository(tmp_path / "voice-latency.sqlite")
    for index in range(21):
        repository.record(
            _sample(index, release_to_play_ms=float((index + 4) * 1000))
        )

    summary = repository.summarize()

    assert summary.sample_size == 20
    assert summary.gate_ready is True
    assert summary.release_to_play_p50_ms == 14500
    assert summary.release_to_play_p95_ms == 23000


def test_empty_summary_is_not_gate_ready(tmp_path: Path) -> None:
    summary = SqliteVoiceLatencyRepository(tmp_path / "voice-latency.sqlite").summarize()

    assert summary.sample_size == 0
    assert summary.gate_ready is False
    assert summary.release_to_play_p50_ms is None
