"""Immutable local events that give Winter evidence-backed lived continuity."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import TypeAlias
from uuid import uuid4
from zoneinfo import ZoneInfo

WINTER_TIMEZONE = ZoneInfo("Asia/Seoul")
EventValue: TypeAlias = str | int | float | bool | None

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS winter_events (
    id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    source TEXT NOT NULL,
    source_ref TEXT,
    occurred_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    metadata TEXT NOT NULL
)
"""


class WinterEventError(ValueError):
    """Raised when an event would not be stable, private or auditable."""


class WinterEventRepositoryError(RuntimeError):
    """Raised when lived-continuity evidence cannot be persisted."""


@dataclass(frozen=True)
class WinterEvent:
    id: str
    event_key: str
    kind: str
    subject: str
    source: str
    source_ref: str | None
    occurred_at: str
    local_date: str
    metadata: tuple[tuple[str, EventValue], ...]


@dataclass(frozen=True)
class WinterDay:
    local_date: str
    event_count: int
    kind_counts: tuple[tuple[str, int], ...]
    source_counts: tuple[tuple[str, int], ...]
    subjects: tuple[str, ...]


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(WINTER_TIMEZONE)


class SqliteWinterEventRepository:
    """Append-only event evidence; conversation text remains in its own DB."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def record(
        self,
        *,
        event_key: str,
        kind: str,
        subject: str,
        source: str,
        occurred_at: datetime,
        source_ref: str | None = None,
        metadata: Mapping[str, EventValue] | None = None,
    ) -> WinterEvent:
        event_key = _required(event_key, "event_key")
        kind = _required(kind, "kind")
        subject = _required(subject, "subject")
        source = _required(source, "source")
        source_ref = source_ref.strip() if source_ref and source_ref.strip() else None
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise WinterEventError("winter event occurred_at must be timezone-aware")
        normalized = _metadata(metadata or {})
        occurred = occurred_at.isoformat()
        local_date = occurred_at.astimezone(WINTER_TIMEZONE).date().isoformat()
        event = WinterEvent(
            id=str(uuid4()),
            event_key=event_key,
            kind=kind,
            subject=subject,
            source=source,
            source_ref=source_ref,
            occurred_at=occurred,
            local_date=local_date,
            metadata=normalized,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO winter_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _values(event),
                )
                row = connection.execute(
                    "SELECT * FROM winter_events WHERE event_key = ?", (event_key,)
                ).fetchone()
        except sqlite3.Error as error:
            raise WinterEventRepositoryError("could not record Winter event") from error
        if row is None:
            raise WinterEventRepositoryError("recorded Winter event could not be read")
        stored = _from_row(row)
        if _identity(stored) != _identity(event):
            raise WinterEventError(
                f"winter event key {event_key!r} was reused with different evidence"
            )
        return stored

    def get(self, event_id: str) -> WinterEvent:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM winter_events WHERE id = ?", (event_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise WinterEventRepositoryError("could not read Winter event") from error
        if row is None:
            raise WinterEventRepositoryError(f"Winter event not found: {event_id}")
        return _from_row(row)

    def find_by_source_ref(self, source_ref: str) -> WinterEvent | None:
        source_ref = _required(source_ref, "source_ref")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM winter_events WHERE source_ref = ? "
                    "ORDER BY occurred_at, id LIMIT 2",
                    (source_ref,),
                ).fetchall()
        except sqlite3.Error as error:
            raise WinterEventRepositoryError(
                "could not find Winter event by source reference"
            ) from error
        if len(rows) > 1:
            raise WinterEventRepositoryError(
                f"multiple Winter events use source reference {source_ref!r}"
            )
        return _from_row(rows[0]) if rows else None

    def list_day(self, local_date: str) -> tuple[WinterEvent, ...]:
        try:
            datetime.fromisoformat(local_date)
        except ValueError as error:
            raise WinterEventError("winter event local_date must be ISO format") from error
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM winter_events WHERE local_date = ? "
                    "ORDER BY occurred_at, id",
                    (local_date,),
                ).fetchall()
        except sqlite3.Error as error:
            raise WinterEventRepositoryError("could not list Winter day") from error
        return tuple(_from_row(row) for row in rows)

    def list_recent(self, limit: int = 50) -> tuple[WinterEvent, ...]:
        if limit < 1:
            raise WinterEventError("winter event limit must be positive")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM winter_events ORDER BY occurred_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as error:
            raise WinterEventRepositoryError("could not list recent Winter events") from error
        return tuple(_from_row(row) for row in rows)

    def list_dates_before(self, local_date: str) -> tuple[str, ...]:
        try:
            datetime.fromisoformat(local_date)
        except ValueError as error:
            raise WinterEventError("winter event local_date must be ISO format") from error
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT DISTINCT local_date FROM winter_events "
                    "WHERE local_date < ? ORDER BY local_date",
                    (local_date,),
                ).fetchall()
        except sqlite3.Error as error:
            raise WinterEventRepositoryError(
                "could not list Winter event dates"
            ) from error
        return tuple(row["local_date"] for row in rows)

    def summarize_day(self, local_date: str) -> WinterDay:
        events = self.list_day(local_date)
        return WinterDay(
            local_date=local_date,
            event_count=len(events),
            kind_counts=tuple(sorted(Counter(event.kind for event in events).items())),
            source_counts=tuple(sorted(Counter(event.source for event in events).items())),
            subjects=tuple(sorted({event.subject for event in events})),
        )

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(_CREATE_EVENTS)
        except (OSError, sqlite3.Error) as error:
            raise WinterEventRepositoryError("could not initialize Winter events") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


def _required(value: str, name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise WinterEventError(f"winter event {name} is required")
    return stripped


def _metadata(values: Mapping[str, EventValue]) -> tuple[tuple[str, EventValue], ...]:
    forbidden = {"text", "user_text", "response_text", "transcript", "audio"}
    if forbidden & set(values):
        raise WinterEventError("winter event metadata must not duplicate conversation content")
    normalized: list[tuple[str, EventValue]] = []
    for key, value in values.items():
        key = _required(key, "metadata key")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise WinterEventError(f"winter event metadata {key!r} must be scalar")
        normalized.append((key, value))
    return tuple(sorted(normalized))


def _values(event: WinterEvent) -> tuple[object, ...]:
    return (
        event.id,
        event.event_key,
        event.kind,
        event.subject,
        event.source,
        event.source_ref,
        event.occurred_at,
        event.local_date,
        json.dumps(dict(event.metadata), ensure_ascii=False, sort_keys=True),
    )


def _from_row(row: sqlite3.Row) -> WinterEvent:
    raw = dict(row)
    try:
        metadata = json.loads(raw.pop("metadata"))
    except (TypeError, json.JSONDecodeError) as error:
        raise WinterEventRepositoryError("Winter event metadata is invalid JSON") from error
    if not isinstance(metadata, dict):
        raise WinterEventRepositoryError("Winter event metadata must be a JSON object")
    return WinterEvent(metadata=_metadata(metadata), **raw)


def _identity(event: WinterEvent) -> tuple[object, ...]:
    return (
        event.event_key,
        event.kind,
        event.subject,
        event.source,
        event.source_ref,
        event.occurred_at,
        event.local_date,
        event.metadata,
    )
