"""Standalone local schedule storage. No Core, model, or production-path bootstrap."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import re
import sqlite3
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from companion.conversation_recall import _terms
from companion.ports import Clock

KST = ZoneInfo("Asia/Seoul")
Operation = Literal["create", "update", "cancel", "query"]
ReceiptStatus = Literal["applied", "needs_clarification", "rejected", "failed"]
Fields = Mapping[str, str | None]
CALENDAR_DATE = re.compile(r'(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일')
DATE_EXPRESSIONS = re.compile(
    r'\d{4}-\d{2}-\d{2}|(?:이번 주 |다음 주 )?[월화수목금토일]요일|이번 주말|오늘|내일|모레|'
    r'(?:\d{4}년\s*)?\d{1,2}월\s*\d{1,2}일'
)

SCHEDULES_DDL = """
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    date TEXT NOT NULL,
    time TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Seoul' CHECK(timezone = 'Asia/Seoul'),
    place TEXT,
    participants_text TEXT,
    status TEXT NOT NULL CHECK(status IN ('planned', 'changed', 'cancelled')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    source_message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
CHANGE_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS change_history (
    command_id TEXT NOT NULL UNIQUE,
    schedule_id TEXT NOT NULL REFERENCES schedules(id),
    before_revision INTEGER NOT NULL,
    after_revision INTEGER NOT NULL,
    change_json TEXT NOT NULL CHECK(json_valid(change_json)),
    source_message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(schedule_id, after_revision),
    CHECK(after_revision = before_revision + 1)
)
"""


@dataclass(frozen=True)
class Schedule:
    id: str
    title: str
    date: str
    time: str | None
    timezone: str
    place: str | None
    participants_text: str | None
    status: str
    revision: int
    source_message_id: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ScheduleCommand:
    operation: Operation
    command_id: str
    source_message_id: int
    cue: str
    fields: Fields = field(default_factory=dict)
    target: Mapping[str, str] = field(default_factory=dict)
    expected_revision: int | None = None
    received_at: datetime | None = None
    original_source_message_id: int | None = None


@dataclass(frozen=True)
class ChangeReceipt:
    status: ReceiptStatus
    operation: Operation
    command_id: str
    source_message_id: int
    schedule_id: str | None = None
    before_revision: int | None = None
    after_revision: int | None = None
    changed_fields: Fields = field(default_factory=dict)
    schedules: tuple[Schedule, ...] = ()
    reason_code: str | None = None
    clarification: str | None = None

    @property
    def revision(self) -> int | None:
        return self.after_revision

    @property
    def reason(self) -> str | None:
        return self.reason_code


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    return now.astimezone(KST)


def resolve_date_range(expression: str, received_at: datetime) -> tuple[date, date] | None:
    """Exact supported expressions only; a week starts on Monday in KST."""
    today = _aware(received_at).date()
    expression = expression.strip()
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expression):
            day = date.fromisoformat(expression)
        elif expression in {"오늘", "내일", "모레"}:
            day = today + timedelta(days={"오늘": 0, "내일": 1, "모레": 2}[expression])
        elif expression == "이번 주말":
            saturday = today + timedelta(days=5 - today.weekday())
            return saturday, saturday + timedelta(days=1)
        elif match := CALENDAR_DATE.fullmatch(expression):
            year, month, day_text = match.groups()
            day = date(int(year) if year else today.year, int(month), int(day_text))
        else:
            match = re.fullmatch(r"(?:(이번 주|다음 주) )?([월화수목금토일])요일", expression)
            if match is None:
                return None
            week, weekday = match.groups()
            offset = "월화수목금토일".index(weekday) - today.weekday()
            if week is None:
                if offset == 0:
                    return None
                offset %= 7
            elif week == "다음 주":
                offset += 7
            day = today + timedelta(days=offset)
        return day, day
    except (ValueError, OverflowError):
        return None


def resolve_date(expression: str, received_at: datetime) -> date | None:
    """Return None for a multi-day expression, including '이번 주말'."""
    span = resolve_date_range(expression, received_at)
    return span[0] if span is not None and span[0] == span[1] else None


def resolve_time(expression: str) -> str | None:
    expression = expression.strip()
    if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", expression):
        return expression
    match = re.fullmatch(r"(오전|오후) ([0-9]{1,2})시(?: ([0-9]{1,2})분)?", expression)
    if match:
        period, hour_text, minute_text = match.groups()
        hour, minute = int(hour_text), int(minute_text or 0)
        if 1 <= hour <= 12 and 0 <= minute < 60:
            return f"{hour % 12 + (12 if period == '오후' else 0):02d}:{minute:02d}"
    return None


def cue_matches(cue: object, source_text: str) -> bool:
    return isinstance(cue, str) and bool(cue.strip()) and cue in source_text


def ungrounded_create_fields(fields: Mapping[str, str | None], source_text: str) -> bool:
    """Every supplied identifying field needs its own current-turn core tokens.

    Strip only common Korean particles, not arbitrary word stems. Titles may contain
    glue words (보러/가는/가기); dates/times are normalized by the existing service.
    """
    glue = {'보러', '가는', '가기', '하는', '하기', '일정', '예약', '약속'}
    for key in ('title', 'place', 'participants'):
        value = fields.get(key)
        if not value or value.casefold() in source_text.casefold():
            continue
        tokens = re.findall(r'[가-힣A-Za-z0-9]+', value)
        tokens = [re.sub(r'(?:에서|으로|이랑|하고|랑|를|을|와|과)$', '', t) for t in tokens]
        tokens = [t for t in tokens if t and (key != 'title' or t not in glue)]
        if not tokens or any(t.casefold() not in source_text.casefold() for t in tokens):
            return True
    return False


class ScheduleRepository:
    """Explicit path only. One connection per operation, no legacy schema migration."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with self._connection() as connection, connection:
            connection.execute(SCHEDULES_DDL)
            connection.execute(CHANGE_HISTORY_DDL)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection

    def get(self, schedule_id: str) -> Schedule | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,),
            ).fetchone()
            return Schedule(**dict(row)) if row else None

    def query(
        self, *, date_from: str | None = None, date_to: str | None = None,
        title_hint: str | None = None, active_only: bool = False,
    ) -> tuple[Schedule, ...]:
        """Inclusive normalized ISO range; cancelled rows are visible by default."""
        if (date_from is None) != (date_to is None):
            raise ValueError("Both date bounds are required")
        if date_from is not None and date_to is not None:
            for bound in (date_from, date_to):
                if date.fromisoformat(bound).isoformat() != bound:
                    raise ValueError("Expected ISO YYYY-MM-DD")
            if date_from > date_to:
                raise ValueError("Reversed date range")
        with self._connection() as connection:
            return self._query(connection, date_from, date_to, title_hint, active_only)

    @staticmethod
    def _query(
        connection: sqlite3.Connection, date_from: str | None = None,
        date_to: str | None = None, title_hint: str | None = None,
        active_only: bool = False, schedule_id: str | None = None,
    ) -> tuple[Schedule, ...]:
        rows = connection.execute(
            "SELECT * FROM schedules WHERE (? IS NULL OR date >= ?) "
            "AND (? IS NULL OR date <= ?) "
            "AND (? IS NULL OR instr(lower(title), lower(?)) > 0) "
            "AND (? = 0 OR status != 'cancelled') AND (? IS NULL OR id = ?) "
            "ORDER BY date, time, id",
            (date_from, date_from, date_to, date_to, title_hint, title_hint,
             active_only, schedule_id, schedule_id),
        ).fetchall()
        return tuple(Schedule(**dict(row)) for row in rows)

    def history(self, schedule_id: str) -> tuple[dict[str, object], ...]:
        with self._connection() as connection:
            return tuple(dict(row) for row in connection.execute(
                "SELECT * FROM change_history WHERE schedule_id = ? ORDER BY after_revision",
                (schedule_id,),
            ))

    def get_revision(self, schedule_id: str, revision: int) -> Schedule | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT change_json FROM change_history "
                "WHERE schedule_id = ? AND after_revision = ?",
                (schedule_id, revision),
            ).fetchone()
            return Schedule(**json.loads(row[0])["after"]) if row else None

    @staticmethod
    def _replay(
        connection: sqlite3.Connection, command_id: str, request: dict[str, object],
        receipt: ChangeReceipt,
    ) -> ChangeReceipt | None:
        row = connection.execute(
            "SELECT change_json FROM change_history WHERE command_id = ?", (command_id,),
        ).fetchone()
        if row is None:
            return None
        saved = json.loads(row[0])
        if saved["command"]["request"] != request:
            return replace(receipt, status="rejected", reason_code="command_id_conflict")
        values = saved["receipt"]
        values["schedules"] = tuple(Schedule(**item) for item in values["schedules"])
        return ChangeReceipt(**values)

    @staticmethod
    def _persist(
        connection: sqlite3.Connection, before: Schedule | None, after: Schedule,
        command: dict[str, object], receipt: ChangeReceipt,
    ) -> None:
        values = asdict(after)
        if before is None:
            columns = ", ".join(values)
            placeholders = ", ".join("?" for _ in values)
            connection.execute(
                f"INSERT INTO schedules ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
        else:
            assignments = ", ".join(f"{name} = ?" for name in values)
            connection.execute(
                f"UPDATE schedules SET {assignments} WHERE id = ? AND revision = ?",
                (*values.values(), before.id, before.revision),
            )
        payload = {
            "command": command, "before": asdict(before) if before else None,
            "after": values, "patch": dict(receipt.changed_fields), "receipt": asdict(receipt),
        }
        connection.execute(
            "INSERT INTO change_history VALUES (?, ?, ?, ?, ?, ?, ?)",
            (receipt.command_id, after.id, receipt.before_revision, after.revision,
             json.dumps(payload, ensure_ascii=False, sort_keys=True),
             after.source_message_id, after.updated_at),
        )


class _Invalid(ValueError):
    def __init__(
        self, reason: str, status: ReceiptStatus = "rejected", clarification: str | None = None,
    ) -> None:
        self.reason, self.status, self.clarification = reason, status, clarification


class ScheduleService:
    """Validate/execute an explicit command. The caller owns intent and source existence."""

    def __init__(self, repository: ScheduleRepository, clock: Clock) -> None:
        self.repository, self.clock = repository, clock

    def match_target(
        self, target: Mapping[str, str], received_at: datetime,
        *, connection: sqlite3.Connection | None = None,
    ) -> tuple[Schedule, ...]:
        """One resolver for revision lookup and the transactional write check.

        Explicit IDs can address cancelled rows; discovery only uses active rows
        within the inclusive KST today +/-60-day window. Never rank/pick a tie.
        """
        if connection is None:
            with self.repository._connection() as reader:
                return self.match_target(target, received_at, connection=reader)
        today = _aware(received_at).date()
        rows = self.repository._query(
            connection, date_from=(today - timedelta(days=60)).isoformat(),
            date_to=(today + timedelta(days=60)).isoformat(),
            schedule_id=target.get('schedule_id'), active_only='schedule_id' not in target,
        )
        if 'schedule_id' in target:
            return rows
        hint = target.get('title_hint', '').strip().casefold()
        if not hint:
            return ()
        exact = tuple(row for row in rows if row.title.casefold() == hint)
        if exact:
            return exact
        substring = tuple(row for row in rows if hint in row.title.casefold())
        if substring:
            return substring
        # Extract date spans before tokenization so "9월 20일"/"다음 주 금요일"
        # remain one calendar constraint. Bare weekdays match any such day in range.
        dates = tuple(match.group() for match in DATE_EXPRESSIONS.finditer(hint))
        terms = _terms(DATE_EXPRESSIONS.sub(' ', hint), canonical=True)
        # A date suffix (금요일로) is left after span extraction and has length <2.
        if not terms and not dates:
            return ()

        def matches(row: Schedule) -> bool:
            day = date.fromisoformat(row.date)
            for expression in dates:
                if expression in {f'{v}요일' for v in '월화수목금토일'}:
                    if day.weekday() != '월화수목금토일'.index(expression[0]):
                        return False
                elif resolve_date(expression, received_at) != day:
                    return False
            values = _terms(' '.join((row.title, row.participants_text or '', row.place or '')),
                            canonical=True)
            return all(any(term in value for value in values) for term in terms)

        return tuple(row for row in rows if matches(row))

    def execute(
        self, command: ScheduleCommand, *, source_text: str, active_only: bool = False,
        allow_cue_fallback: bool = False,
    ) -> ChangeReceipt:
        """Query is read-only; other operations atomically check, resolve and write.

        source_text verifies cue or P2.1 field grounding and is never persisted.
        allow_cue_fallback is selected by Core, never by a model-owned field.
        received_at may be supplied by the future Core, otherwise Clock is sampled once.
        """
        receipt = ChangeReceipt(
            "rejected", command.operation, command.command_id, command.source_message_id,
        )
        try:
            self._validate(command, source_text, active_only, allow_cue_fallback)
            request: dict[str, object] = {
                "operation": command.operation, "target": dict(command.target),
                "fields": dict(command.fields), "expected_revision": command.expected_revision,
                "source_message_id": command.source_message_id, "cue": command.cue,
                "original_source_message_id": command.original_source_message_id,
                "received_at": (_aware(command.received_at).astimezone(UTC).isoformat()
                                if command.received_at is not None else None),
            }
            if allow_cue_fallback:
                request["cue_policy"] = "optional_with_field_grounding"
            if command.operation == "query":
                now = _aware(command.received_at or self.clock.now())
                start, end = self._query_dates(command.fields, now)
                schedules = self.repository.query(
                    date_from=start, date_to=end, title_hint=command.target.get("title_hint"),
                    active_only=active_only,
                )
                return replace(receipt, status="applied", schedules=schedules)
            try:
                with self.repository.transaction() as connection:
                    replay = self.repository._replay(
                        connection, command.command_id, request, receipt,
                    )
                    if replay is not None:
                        return replay
                    now = _aware(command.received_at or self.clock.now())
                    result = self._change(connection, command, request, now, receipt,
                                          source_text if allow_cue_fallback else None)
                return result  # Only return applied after the transaction commits.
            except sqlite3.Error:
                # Closing/rolling back the write connection precedes this independent read.
                try:
                    with self.repository._connection() as connection:
                        replay = self.repository._replay(
                            connection, command.command_id, request, receipt,
                        )
                    return replay or replace(receipt, status="failed", reason_code="database_error")
                except sqlite3.Error:
                    return replace(receipt, status="failed", reason_code="commit_unconfirmed")
        except _Invalid as error:
            return replace(receipt, status=error.status, reason_code=error.reason,
                           clarification=error.clarification)
        except (ValueError, OverflowError):
            return replace(receipt, reason_code="invalid_received_at")
        except sqlite3.Error:
            return replace(receipt, status="failed", reason_code="database_error")

    @staticmethod
    def _validate(
        command: ScheduleCommand, source_text: str, active_only: bool,
        allow_cue_fallback: bool = False,
    ) -> None:
        if command.operation not in {"create", "update", "cancel", "query"}:
            raise _Invalid("invalid_operation")
        if not isinstance(command.command_id, str) or not command.command_id.strip():
            raise _Invalid("invalid_command_id")
        if not isinstance(active_only, bool):
            raise _Invalid("invalid_filter")
        if command.received_at is not None and not isinstance(command.received_at, datetime):
            raise _Invalid("invalid_received_at")
        sources = [command.source_message_id]
        if command.original_source_message_id is not None:
            sources.append(command.original_source_message_id)
        for source in sources:
            if type(source) is not int or source <= 0:
                raise _Invalid("invalid_source")
        if not allow_cue_fallback and (
            not isinstance(command.cue, str) or not 2 <= len(command.cue.strip()) <= 160
            or command.cue not in source_text
        ):
            raise _Invalid("invalid_cue")
        if not isinstance(command.fields, Mapping) or not isinstance(command.target, Mapping):
            raise _Invalid("invalid_schema")
        if any(not isinstance(v, str) or not v.strip() for v in command.target.values()):
            raise _Invalid("invalid_target")
        if any(v is not None and not isinstance(v, str) for v in command.fields.values()):
            raise _Invalid("invalid_fields")
        if (allow_cue_fallback and not cue_matches(command.cue, source_text)
                and command.operation == "create"
                and ungrounded_create_fields(command.fields, source_text)):
            raise _Invalid("ungrounded_fields")
        allowed = {"title", "date", "time", "place", "participants"}
        if command.operation == "query":
            if (set(command.fields) - {"date_from", "date_to"}
                    or set(command.target) - {"title_hint"}
                    or command.expected_revision is not None):
                raise _Invalid("invalid_query")
            if not command.fields and not command.target:
                raise _Invalid("missing_query_filter", "needs_clarification", "어떤 일정을 찾을까?")
        else:
            if active_only:
                raise _Invalid("invalid_filter")
            if set(command.fields) - allowed or set(command.target) - {"schedule_id", "title_hint"}:
                raise _Invalid("invalid_schema")
            if type(command.expected_revision) is not int or command.expected_revision < 0:
                raise _Invalid("invalid_revision")
            if command.operation == "create":
                if command.target or command.expected_revision != 0:
                    raise _Invalid("invalid_create")
            elif not command.target:
                raise _Invalid("missing_target", "needs_clarification", "어느 일정을 바꿀까?")
            if command.operation == "cancel" and command.fields:
                raise _Invalid("invalid_cancel")
            if command.operation == "update" and not command.fields:
                raise _Invalid("missing_fields", "needs_clarification", "어떤 내용을 바꿀까?")

    @staticmethod
    def _day(expression: str, now: datetime, *, writing: bool) -> str:
        day = resolve_date(expression, now)
        if day is None:
            if re.fullmatch(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}", expression.strip()):
                raise _Invalid("invalid_date")
            raise _Invalid("ambiguous_date", "needs_clarification", "정확히 어느 날짜로 할까?")
        if writing and day < now.date():
            raise _Invalid("past_date", "needs_clarification", "지난 날짜로 기록하려는 거야?")
        return day.isoformat()

    @classmethod
    def _query_dates(cls, fields: Fields, now: datetime) -> tuple[str | None, str | None]:
        if not fields:
            return None, None
        if set(fields) != {"date_from", "date_to"} or any(v is None for v in fields.values()):
            raise _Invalid("invalid_date_range")
        bounds = []
        for key, index in (("date_from", 0), ("date_to", 1)):
            expression = fields[key]
            assert expression is not None
            span = resolve_date_range(expression, now)
            if span is None:
                cls._day(expression, now, writing=False)  # Distinguish invalid and ambiguous.
            assert span is not None
            bounds.append(span[index].isoformat())
        if bounds[0] > bounds[1]:
            raise _Invalid("invalid_date_range")
        return bounds[0], bounds[1]

    @classmethod
    def _normalize(
        cls, command: ScheduleCommand, now: datetime, source_text: str | None = None,
    ) -> dict[str, str | None]:
        values = dict(command.fields)
        if command.operation == "create":
            for name in ("title", "date"):
                if name not in values:
                    raise _Invalid(
                        f"missing_{name}", "needs_clarification", f"{name} 값을 알려줄래?"
                    )
        for name, value in tuple(values.items()):
            if name in {"title", "date"} and (value is None or not value.strip()):
                raise _Invalid(f"invalid_{name}")
            if name == "date":
                assert value is not None
                normalized_day = cls._day(value, now, writing=True)
                values[name] = normalized_day
                # P1 callers supply trusted commands. Model-originated Core commands
                # additionally compare ISO calculations with the CURRENT user turn.
                if source_text is not None and value.strip() == normalized_day:
                    mentions = tuple(DATE_EXPRESSIONS.finditer(source_text))
                    if not mentions:
                        raise _Invalid('ungrounded_date', 'needs_clarification',
                                       '정확히 어느 날짜로 할까?')
                    days = {resolve_date(m.group(), now) for m in mentions}
                    if days != {date.fromisoformat(normalized_day)}:
                        raise _Invalid('date_mismatch', 'needs_clarification',
                                       '날짜 계산이 원문과 맞지 않아. 정확히 어느 날짜로 할까?')
            elif name == "time" and value is not None:
                normalized = resolve_time(value)
                if normalized is None:
                    if re.fullmatch(r"[0-9]{1,2}:[0-9]{2}", value.strip()):
                        raise _Invalid("invalid_time")
                    raise _Invalid("ambiguous_time", "needs_clarification", "몇 시인지 알려줄래?")
                values[name] = normalized
            elif value is not None:
                values[name] = value.strip()
        if "participants" in values:
            values["participants_text"] = values.pop("participants")
        return values

    def _change(
        self, connection: sqlite3.Connection, command: ScheduleCommand,
        request: dict[str, object], now: datetime, receipt: ChangeReceipt,
        source_text: str | None = None,
    ) -> ChangeReceipt:
        before = None
        if command.operation != "create":
            candidates = self.match_target(command.target, now, connection=connection)
            if not candidates:
                return replace(receipt, reason_code="target_not_found")
            if len(candidates) > 1:
                return replace(receipt, status="needs_clarification", schedules=candidates,
                               reason_code="ambiguous_target", clarification="어느 일정을 바꿀까?")
            before = candidates[0]
            receipt = replace(receipt, schedule_id=before.id, before_revision=before.revision,
                              after_revision=before.revision, schedules=candidates)
            if command.expected_revision != before.revision:
                return replace(receipt, reason_code="revision_conflict")
            if before.status == "cancelled":
                return replace(receipt, reason_code="already_cancelled")
        try:
            normalized = self._normalize(command, now, source_text)
        except _Invalid as error:
            return replace(receipt, status=error.status, reason_code=error.reason,
                           clarification=error.clarification)
        stamp = now.astimezone(UTC).isoformat()
        if before is None:
            after = Schedule(
                id=str(uuid4()), title=str(normalized["title"]), date=str(normalized["date"]),
                time=normalized.get("time"), timezone="Asia/Seoul", place=normalized.get("place"),
                participants_text=normalized.get("participants_text"), status="planned",
                revision=1, source_message_id=command.source_message_id,
                created_at=stamp, updated_at=stamp,
            )
            patch = {name: getattr(after, name) for name in (
                "title", "date", "time", "place", "participants_text", "status",
            )}
        else:
            patch = {
                key: value for key, value in normalized.items() if getattr(before, key) != value
            }
            if command.operation == "update" and not patch:
                return replace(receipt, reason_code="no_change")
            status = "cancelled" if command.operation == "cancel" else "changed"
            if before.status != status:
                patch["status"] = status
            after = Schedule(**{
                **asdict(before), **patch, "revision": before.revision + 1,
                "source_message_id": command.source_message_id, "updated_at": stamp,
            })
        result = replace(
            receipt, status="applied", schedule_id=after.id,
            before_revision=before.revision if before else 0, after_revision=after.revision,
            changed_fields=patch, schedules=(after,),
        )
        self.repository._persist(connection, before, after, {
            "request": request, "received_at": stamp, "normalized_fields": normalized,
        }, result)
        return result
