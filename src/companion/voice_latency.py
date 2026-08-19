"""Privacy-minimal phone Voice latency traces and a 20-turn gate summary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import math
from pathlib import Path
import sqlite3
from statistics import median

TARGET_SAMPLE_SIZE = 20


class VoiceLatencyError(ValueError):
    """Raised when a browser trace cannot represent one completed Voice turn."""


class VoiceLatencyRepositoryError(RuntimeError):
    """Raised when Voice latency evidence cannot be persisted."""


@dataclass(frozen=True)
class VoiceLatencySample:
    trace_id: str
    release_to_stt_ms: float
    release_to_first_sentence_ms: float
    release_to_first_audio_ms: float
    release_to_play_ms: float
    server_stt_ms: float
    server_first_sentence_ms: float
    server_first_audio_ms: float
    created_at: str

    def __post_init__(self) -> None:
        trace_id = self.trace_id.strip()
        if not trace_id or len(trace_id) > 128:
            raise VoiceLatencyError("voice trace_id must contain 1 to 128 characters")
        client_order = (
            self.release_to_stt_ms,
            self.release_to_first_sentence_ms,
            self.release_to_first_audio_ms,
            self.release_to_play_ms,
        )
        server_order = (self.server_first_sentence_ms, self.server_first_audio_ms)
        for name, value in _numeric_fields(self).items():
            if not math.isfinite(value) or value < 0:
                raise VoiceLatencyError(f"{name} must be a finite non-negative number")
        if tuple(sorted(client_order)) != client_order:
            raise VoiceLatencyError("client Voice milestones must be monotonic")
        if tuple(sorted(server_order)) != server_order:
            raise VoiceLatencyError("server Voice milestones must be monotonic")
        try:
            datetime.fromisoformat(self.created_at)
        except ValueError as error:
            raise VoiceLatencyError("created_at must be an ISO datetime") from error

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> VoiceLatencySample:
        expected = {
            "trace_id",
            "release_to_stt_ms",
            "release_to_first_sentence_ms",
            "release_to_first_audio_ms",
            "release_to_play_ms",
            "server_stt_ms",
            "server_first_sentence_ms",
            "server_first_audio_ms",
        }
        if set(payload) != expected:
            missing = sorted(expected - set(payload))
            extra = sorted(set(payload) - expected)
            raise VoiceLatencyError(
                f"voice latency fields mismatch: missing={missing}, extra={extra}"
            )
        trace_id = payload["trace_id"]
        if not isinstance(trace_id, str):
            raise VoiceLatencyError("voice trace_id must be a string")
        values: dict[str, float] = {}
        for name in expected - {"trace_id"}:
            value = payload[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise VoiceLatencyError(f"{name} must be numeric")
            values[name] = float(value)
        return cls(
            trace_id=trace_id,
            created_at=datetime.now(UTC).isoformat(),
            **values,
        )


@dataclass(frozen=True)
class VoiceLatencySummary:
    sample_size: int
    target_sample_size: int
    gate_ready: bool
    release_to_play_p50_ms: float | None
    release_to_play_p95_ms: float | None
    release_to_stt_p50_ms: float | None
    release_to_stt_p95_ms: float | None
    server_first_sentence_p50_ms: float | None
    server_first_audio_p50_ms: float | None

    def to_dict(self) -> dict[str, int | float | bool | None]:
        return asdict(self)


class SqliteVoiceLatencyRepository:
    _SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS voice_latency_schema "
                    "(version INTEGER NOT NULL)"
                )
                row = connection.execute(
                    "SELECT version FROM voice_latency_schema LIMIT 1"
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO voice_latency_schema (version) VALUES (?)",
                        (self._SCHEMA_VERSION,),
                    )
                elif int(row[0]) != self._SCHEMA_VERSION:
                    raise VoiceLatencyRepositoryError(
                        f"unsupported voice latency schema version {row[0]}"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS voice_latency_samples (
                        trace_id TEXT PRIMARY KEY,
                        release_to_stt_ms REAL NOT NULL,
                        release_to_first_sentence_ms REAL NOT NULL,
                        release_to_first_audio_ms REAL NOT NULL,
                        release_to_play_ms REAL NOT NULL,
                        server_stt_ms REAL NOT NULL,
                        server_first_sentence_ms REAL NOT NULL,
                        server_first_audio_ms REAL NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
        except sqlite3.Error as error:
            raise VoiceLatencyRepositoryError(
                f"could not initialize Voice latency DB at {path}"
            ) from error

    def record(self, sample: VoiceLatencySample) -> bool:
        """Store one completed turn; browser retries are idempotent by trace ID."""
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO voice_latency_samples (
                        trace_id, release_to_stt_ms,
                        release_to_first_sentence_ms, release_to_first_audio_ms,
                        release_to_play_ms, server_stt_ms,
                        server_first_sentence_ms, server_first_audio_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sample.trace_id,
                        sample.release_to_stt_ms,
                        sample.release_to_first_sentence_ms,
                        sample.release_to_first_audio_ms,
                        sample.release_to_play_ms,
                        sample.server_stt_ms,
                        sample.server_first_sentence_ms,
                        sample.server_first_audio_ms,
                        sample.created_at,
                    ),
                )
                return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise VoiceLatencyRepositoryError("could not record Voice latency") from error

    def list_recent(self, limit: int = TARGET_SAMPLE_SIZE) -> tuple[VoiceLatencySample, ...]:
        if limit < 1:
            raise ValueError("Voice latency limit must be positive")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT trace_id, release_to_stt_ms,
                           release_to_first_sentence_ms, release_to_first_audio_ms,
                           release_to_play_ms, server_stt_ms,
                           server_first_sentence_ms, server_first_audio_ms, created_at
                    FROM voice_latency_samples
                    ORDER BY created_at DESC, trace_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as error:
            raise VoiceLatencyRepositoryError("could not list Voice latency") from error
        return tuple(VoiceLatencySample(*row) for row in rows)

    def summarize(self, limit: int = TARGET_SAMPLE_SIZE) -> VoiceLatencySummary:
        samples = self.list_recent(limit)
        if not samples:
            return VoiceLatencySummary(
                sample_size=0,
                target_sample_size=TARGET_SAMPLE_SIZE,
                gate_ready=False,
                release_to_play_p50_ms=None,
                release_to_play_p95_ms=None,
                release_to_stt_p50_ms=None,
                release_to_stt_p95_ms=None,
                server_first_sentence_p50_ms=None,
                server_first_audio_p50_ms=None,
            )
        return VoiceLatencySummary(
            sample_size=len(samples),
            target_sample_size=TARGET_SAMPLE_SIZE,
            gate_ready=len(samples) >= TARGET_SAMPLE_SIZE,
            release_to_play_p50_ms=_p50(
                [sample.release_to_play_ms for sample in samples]
            ),
            release_to_play_p95_ms=_p95(
                [sample.release_to_play_ms for sample in samples]
            ),
            release_to_stt_p50_ms=_p50(
                [sample.release_to_stt_ms for sample in samples]
            ),
            release_to_stt_p95_ms=_p95(
                [sample.release_to_stt_ms for sample in samples]
            ),
            server_first_sentence_p50_ms=_p50(
                [sample.server_first_sentence_ms for sample in samples]
            ),
            server_first_audio_p50_ms=_p50(
                [sample.server_first_audio_ms for sample in samples]
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


def _numeric_fields(sample: VoiceLatencySample) -> dict[str, float]:
    return {
        "release_to_stt_ms": sample.release_to_stt_ms,
        "release_to_first_sentence_ms": sample.release_to_first_sentence_ms,
        "release_to_first_audio_ms": sample.release_to_first_audio_ms,
        "release_to_play_ms": sample.release_to_play_ms,
        "server_stt_ms": sample.server_stt_ms,
        "server_first_sentence_ms": sample.server_first_sentence_ms,
        "server_first_audio_ms": sample.server_first_audio_ms,
    }


def _p50(values: list[float]) -> float:
    return round(float(median(values)), 3)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(float(ordered[index]), 3)
