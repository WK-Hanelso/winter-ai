"""A small, grounded bridge from one real Winter day to the next."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Protocol
from uuid import uuid4

from companion.reflection import ConversationRecord, SqliteConversationHistoryReader
from companion.winter_events import (
    WINTER_TIMEZONE,
    SqliteWinterEventRepository,
    WinterEvent,
)

DAY_REFLECTION_KINDS = (
    "shared_moment",
    "cheonu_change",
    "open_thread",
    "winter_reflection",
    "next_cue",
)


class DayReflectionError(ValueError):
    """Raised when a day reflection is ungrounded or not yet eligible."""


class DayReflectionRepositoryError(RuntimeError):
    """Raised when the local day-reflection store cannot be used."""


@dataclass(frozen=True)
class DayConversationMessage:
    id: int
    role: str
    content: str
    occurred_at: str


@dataclass(frozen=True)
class DayReflectionPoint:
    kind: str
    content: str
    evidence_message_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.kind not in DAY_REFLECTION_KINDS:
            raise DayReflectionError(f"unsupported day reflection kind: {self.kind}")
        content = self.content.strip()
        if not content or len(content) > 300:
            raise DayReflectionError(
                "day reflection point content must contain 1 to 300 characters"
            )
        if (
            not self.evidence_message_ids
            or any(item < 1 for item in self.evidence_message_ids)
            or len(self.evidence_message_ids) != len(set(self.evidence_message_ids))
        ):
            raise DayReflectionError(
                "day reflection point requires unique positive evidence ids"
            )


@dataclass(frozen=True)
class DayReflectionRequest:
    local_date: str
    messages: tuple[DayConversationMessage, ...]
    events: tuple[WinterEvent, ...]


@dataclass(frozen=True)
class DayReflectionDraft:
    points: tuple[DayReflectionPoint, ...]


@dataclass(frozen=True)
class WinterDayReflection:
    id: str
    local_date: str
    input_fingerprint: str
    event_ids: tuple[str, ...]
    message_ids: tuple[int, ...]
    points: tuple[DayReflectionPoint, ...]
    created_at: str


@dataclass(frozen=True)
class DayCloseResult:
    reflection: WinterDayReflection
    created: bool


class DayReflectionInterpreter(Protocol):
    def reflect(self, request: DayReflectionRequest) -> DayReflectionDraft: ...


class SqliteConversationDayReader:
    def __init__(self, path: Path) -> None:
        self._history = SqliteConversationHistoryReader(path)

    def list_day(self, local_date: str) -> tuple[DayConversationMessage, ...]:
        target = _parse_date(local_date)
        selected = []
        for record in self._history.list_records():
            occurred = _record_datetime(record)
            if occurred.astimezone(WINTER_TIMEZONE).date() == target:
                selected.append(
                    DayConversationMessage(
                        id=record.id,
                        role=record.role,
                        content=record.content,
                        occurred_at=occurred.isoformat(),
                    )
                )
        return tuple(selected)


class SqliteDayReflectionRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def save(
        self,
        *,
        local_date: str,
        input_fingerprint: str,
        event_ids: tuple[str, ...],
        message_ids: tuple[int, ...],
        points: tuple[DayReflectionPoint, ...],
    ) -> WinterDayReflection:
        reflection = WinterDayReflection(
            id=str(uuid4()),
            local_date=local_date,
            input_fingerprint=input_fingerprint,
            event_ids=event_ids,
            message_ids=message_ids,
            points=points,
            created_at=datetime.now(UTC).isoformat(),
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO day_reflections VALUES (?, ?, ?, ?, ?, ?, ?)",
                    _reflection_values(reflection),
                )
                row = connection.execute(
                    "SELECT * FROM day_reflections WHERE local_date = ?",
                    (local_date,),
                ).fetchone()
        except sqlite3.Error as error:
            raise DayReflectionRepositoryError(
                f"could not save Winter day reflection: {error}"
            ) from error
        if row is None:
            raise DayReflectionRepositoryError("Winter day reflection was not stored")
        stored = _reflection_from_row(row)
        if _reflection_identity(stored) != _reflection_identity(reflection):
            raise DayReflectionError(
                "a closed Winter day cannot be replaced with different evidence"
            )
        return stored

    def get(self, local_date: str) -> WinterDayReflection | None:
        _parse_date(local_date)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM day_reflections WHERE local_date = ?",
                    (local_date,),
                ).fetchone()
        except sqlite3.Error as error:
            raise DayReflectionRepositoryError(
                f"could not read Winter day reflection: {error}"
            ) from error
        return _reflection_from_row(row) if row is not None else None

    def list_before(
        self,
        local_date: str,
        *,
        limit: int = 2,
    ) -> tuple[WinterDayReflection, ...]:
        _parse_date(local_date)
        if limit < 1:
            raise DayReflectionError("day reflection retrieval limit must be positive")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM day_reflections WHERE local_date < ? "
                    "ORDER BY local_date DESC LIMIT ?",
                    (local_date, limit),
                ).fetchall()
        except sqlite3.Error as error:
            raise DayReflectionRepositoryError(
                f"could not list Winter day reflections: {error}"
            ) from error
        return tuple(_reflection_from_row(row) for row in rows)

    def list(self) -> tuple[WinterDayReflection, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM day_reflections ORDER BY local_date DESC"
                ).fetchall()
        except sqlite3.Error as error:
            raise DayReflectionRepositoryError(
                f"could not list Winter day reflections: {error}"
            ) from error
        return tuple(_reflection_from_row(row) for row in rows)

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS day_reflections (
                        id TEXT PRIMARY KEY,
                        local_date TEXT NOT NULL UNIQUE,
                        input_fingerprint TEXT NOT NULL,
                        event_ids_json TEXT NOT NULL,
                        message_ids_json TEXT NOT NULL,
                        points_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            self._path.chmod(0o600)
        except (OSError, sqlite3.Error) as error:
            raise DayReflectionRepositoryError(
                f"could not initialize Winter day reflections: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


class DayReflectionService:
    def __init__(
        self,
        conversations: SqliteConversationDayReader,
        events: SqliteWinterEventRepository,
        interpreter: DayReflectionInterpreter,
        repository: SqliteDayReflectionRepository,
    ) -> None:
        self._conversations = conversations
        self._events = events
        self._interpreter = interpreter
        self._repository = repository

    def close(self, local_date: str, *, now: datetime) -> DayCloseResult:
        target = _parse_date(local_date)
        if now.tzinfo is None or now.utcoffset() is None:
            raise DayReflectionError("day close requires a timezone-aware current time")
        if target >= now.astimezone(WINTER_TIMEZONE).date():
            raise DayReflectionError("only a completed past Winter day can be closed")
        messages = self._conversations.list_day(local_date)
        events = self._events.list_day(local_date)
        if not messages or not events:
            raise DayReflectionError(
                "a Winter day requires both actual conversation and Winter events"
            )
        if {message.role for message in messages} != {"user", "assistant"}:
            raise DayReflectionError("a Winter day requires both sides of the conversation")
        fingerprint = _input_fingerprint(local_date, messages, events)
        existing = self._repository.get(local_date)
        if existing is not None:
            if existing.input_fingerprint != fingerprint:
                raise DayReflectionError(
                    "conversation evidence changed after the Winter day was closed"
                )
            return DayCloseResult(existing, created=False)
        request = DayReflectionRequest(local_date, messages, events)
        draft = self._interpreter.reflect(request)
        points = _validated_points(draft.points, messages)
        reflection = self._repository.save(
            local_date=local_date,
            input_fingerprint=fingerprint,
            event_ids=tuple(event.id for event in events),
            message_ids=tuple(message.id for message in messages),
            points=points,
        )
        return DayCloseResult(reflection, created=True)


class ActiveDayReflectionRetriever:
    def __init__(self, repository: SqliteDayReflectionRepository) -> None:
        self._repository = repository

    def retrieve(self, *, before_date: str) -> tuple[WinterDayReflection, ...]:
        return self._repository.list_before(before_date, limit=2)


def day_reflection_context(reflections: tuple[WinterDayReflection, ...]) -> str:
    lines = [
        "[겨울이의 지난날 회고]",
        "아래는 실제 대화를 근거로 만든 수정 가능한 회고다. 천우의 영구 사실이나 현재 "
        "감정으로 단정하지 말고, 지금 발화와 충돌하면 지금 발화를 우선한다.",
    ]
    labels = {
        "shared_moment": "함께한 일",
        "cheonu_change": "천우 변화 관찰",
        "open_thread": "이어갈 이야기",
        "winter_reflection": "겨울이의 생각",
        "next_cue": "다음 대화 단서",
    }
    for reflection in reversed(reflections):
        lines.append(f"- {reflection.local_date}")
        for point in reflection.points:
            lines.append(f"  - {labels[point.kind]}: {point.content}")
    return "\n".join(lines)


def _validated_points(
    points: tuple[DayReflectionPoint, ...],
    messages: tuple[DayConversationMessage, ...],
) -> tuple[DayReflectionPoint, ...]:
    if not points or len(points) > len(DAY_REFLECTION_KINDS):
        raise DayReflectionError("a day reflection requires 1 to 5 points")
    kinds = [point.kind for point in points]
    if len(kinds) != len(set(kinds)):
        raise DayReflectionError("a day reflection may contain each point kind once")
    message_ids = {message.id for message in messages}
    if any(not set(point.evidence_message_ids) <= message_ids for point in points):
        raise DayReflectionError("day reflection evidence must belong to the same day")
    return tuple(points)


def _record_datetime(record: ConversationRecord) -> datetime:
    raw = record.created_at.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise DayReflectionRepositoryError(
            f"conversation message {record.id} has an invalid timestamp"
        ) from error
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise DayReflectionError("Winter day must use YYYY-MM-DD") from error
    return parsed


def _input_fingerprint(
    local_date: str,
    messages: tuple[DayConversationMessage, ...],
    events: tuple[WinterEvent, ...],
) -> str:
    identity = json.dumps(
        {
            "schema": "winter-day-reflection-v1",
            "local_date": local_date,
            "message_ids": [message.id for message in messages],
            "event_ids": [event.id for event in events],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def _reflection_values(reflection: WinterDayReflection) -> tuple[object, ...]:
    return (
        reflection.id,
        reflection.local_date,
        reflection.input_fingerprint,
        json.dumps(reflection.event_ids),
        json.dumps(reflection.message_ids),
        json.dumps(
            [asdict(point) for point in reflection.points],
            ensure_ascii=False,
            sort_keys=True,
        ),
        reflection.created_at,
    )


def _reflection_from_row(row: sqlite3.Row) -> WinterDayReflection:
    raw = dict(row)
    try:
        event_ids = json.loads(raw.pop("event_ids_json"))
        message_ids = json.loads(raw.pop("message_ids_json"))
        points = json.loads(raw.pop("points_json"))
        return WinterDayReflection(
            event_ids=tuple(event_ids),
            message_ids=tuple(message_ids),
            points=tuple(
                DayReflectionPoint(
                    kind=point["kind"],
                    content=point["content"],
                    evidence_message_ids=tuple(point["evidence_message_ids"]),
                )
                for point in points
            ),
            **raw,
        )
    except (KeyError, TypeError, json.JSONDecodeError, DayReflectionError) as error:
        raise DayReflectionRepositoryError(
            "stored Winter day reflection is invalid"
        ) from error


def _reflection_identity(reflection: WinterDayReflection) -> tuple[object, ...]:
    return (
        reflection.local_date,
        reflection.input_fingerprint,
        reflection.event_ids,
        reflection.message_ids,
        reflection.points,
    )
