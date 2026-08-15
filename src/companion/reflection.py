"""Evidence-backed recovery of conversational memory candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol
from uuid import uuid4

from companion.contracts import ConversationMessage
from companion.memory import Memory, SqliteMemoryRepository
from companion.turn_understanding import (
    MEMORY_KINDS,
    TEMPORAL_SCOPES,
    MemoryProposal,
    ShadowTurnEvent,
    SqliteTurnUnderstandingRepository,
    TurnInterpretationRequest,
)

REFLECTION_STATUSES = frozenset({"candidate", "active", "approved", "rejected"})
OPERATIVE_MEMORY_KINDS = frozenset(
    {"semantic", "preference", "decision", "project", "procedural"}
)
OPERATIVE_TEMPORAL_SCOPES = frozenset({"candidate_stable", "stable"})


class ReflectionRepositoryError(RuntimeError):
    """Raised when recovered candidate state cannot be persisted safely."""


class ReflectionInterpretationError(ValueError):
    """Raised when compact conversational reflection violates its contract."""


class ReflectionInterpreterUnavailableError(ReflectionInterpretationError):
    """A transient local-model failure that should remain pending."""


@dataclass(frozen=True)
class ReflectionExtractionRequest:
    message_id: int
    user_text: str
    recent_context: tuple[ConversationMessage, ...] = ()

    def __post_init__(self) -> None:
        if self.message_id < 1 or not self.user_text.strip():
            raise ReflectionInterpretationError(
                "reflection message id and user text are required"
            )


@dataclass(frozen=True)
class ReflectionExtraction:
    proposals: tuple[MemoryProposal, ...]


class ReflectionInterpreter(Protocol):
    def extract(self, request: ReflectionExtractionRequest) -> ReflectionExtraction: ...


class FakeReflectionInterpreter:
    def extract(self, request: ReflectionExtractionRequest) -> ReflectionExtraction:
        return ReflectionExtraction(())


@dataclass(frozen=True)
class ConversationRecord:
    id: int
    role: str
    content: str
    created_at: str


@dataclass(frozen=True)
class ReflectionEvidence:
    turn_id: str
    quote: str


@dataclass(frozen=True)
class ReflectionCandidate:
    id: str
    kind: str
    content: str
    confidence: float
    temporal_scope: str
    status: str
    evidence: tuple[ReflectionEvidence, ...]
    memory_id: str | None
    created_at: str
    updated_at: str

    @property
    def can_activate_as_memory(self) -> bool:
        return (
            self.kind in OPERATIVE_MEMORY_KINDS
            and self.temporal_scope in OPERATIVE_TEMPORAL_SCOPES
        )


@dataclass(frozen=True)
class HistoryBackfillResult:
    user_messages: int
    already_linked: int
    linked_existing: int
    enqueued: int


@dataclass(frozen=True)
class ReflectionHarvestResult:
    processed_turns: int
    created_candidates: int
    updated_candidates: int


@dataclass(frozen=True)
class ReflectionProcessingResult:
    processed: int
    completed: int
    failed: int
    deferred: int
    created_candidates: int
    updated_candidates: int


class SqliteConversationHistoryReader:
    def __init__(self, path: Path) -> None:
        self._path = path

    def list_records(self) -> tuple[ConversationRecord, ...]:
        try:
            with sqlite3.connect(self._path) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT id, role, content, created_at
                    FROM conversation_messages ORDER BY id
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not read conversation history: {error}"
            ) from error
        records = tuple(
            ConversationRecord(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        )
        if any(record.role not in {"user", "assistant"} for record in records):
            raise ReflectionRepositoryError("conversation history contains an invalid role")
        return records


class ConversationHistoryBackfill:
    """Link existing Shadow turns, then queue only truly missing user messages."""

    def __init__(
        self,
        history: SqliteConversationHistoryReader,
        turns: SqliteTurnUnderstandingRepository,
        *,
        context_messages: int = 12,
    ) -> None:
        if context_messages < 1:
            raise ValueError("context_messages must be at least 1")
        self._history = history
        self._turns = turns
        self._context_messages = context_messages

    def run(self) -> HistoryBackfillResult:
        records = self._history.list_records()
        user_records = tuple(record for record in records if record.role == "user")
        existing_events = list(self._turns.list())
        available: dict[tuple[str, str], list[ShadowTurnEvent]] = {}
        linked_event_ids: set[str] = set()
        already_linked = 0
        for record in user_records:
            linked = self._turns.recovered_event_for(record.id)
            if linked is not None:
                linked_event_ids.add(linked.id)
                already_linked += 1
        for event in existing_events:
            if event.id in linked_event_ids:
                continue
            key = (event.request.user_text, _second(event.created_at))
            available.setdefault(key, []).append(event)

        linked_existing = 0
        enqueued = 0
        for index, record in enumerate(records):
            if record.role != "user" or self._turns.recovered_event_for(record.id):
                continue
            key = (record.content, _second(record.created_at))
            matching = available.get(key, [])
            if matching:
                event = matching.pop(0)
                self._turns.link_recovered_message(
                    conversation_message_id=record.id,
                    event_id=event.id,
                    conversation_created_at=record.created_at,
                )
                linked_existing += 1
                continue
            start = max(0, index - self._context_messages + 1)
            context = tuple(
                ConversationMessage(item.role, item.content)
                for item in records[start : index + 1]
            )
            self._turns.enqueue_recovered(
                TurnInterpretationRequest(record.content, context),
                conversation_message_id=record.id,
                conversation_created_at=record.created_at,
            )
            enqueued += 1
        return HistoryBackfillResult(
            user_messages=len(user_records),
            already_linked=already_linked,
            linked_existing=linked_existing,
            enqueued=enqueued,
        )


class SqliteReflectionRepository:
    _SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def harvest(self, events: tuple[ShadowTurnEvent, ...]) -> ReflectionHarvestResult:
        processed = 0
        created = 0
        updated = 0
        for event in events:
            if event.status != "completed" or event.understanding is None:
                continue
            if self._is_processed(event.id):
                continue
            for proposal in event.understanding.memory_proposals:
                evidence = tuple(
                    ReflectionEvidence(event.id, quote) for quote in proposal.evidence
                )
                was_created = self._upsert_candidate(
                    kind=proposal.kind,
                    content=proposal.content,
                    confidence=proposal.confidence,
                    temporal_scope=proposal.temporal_scope,
                    evidence=evidence,
                )
                created += int(was_created)
                updated += int(not was_created)
            self._mark_processed(event.id)
            processed += 1
        return ReflectionHarvestResult(processed, created, updated)

    def has_extraction(self, message_id: int) -> bool:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM reflection_extractions
                    WHERE message_id = ? AND status = 'completed'
                    """,
                    (message_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not read reflection extraction: {error}"
            ) from error
        return row is not None

    def record_extraction(
        self,
        request: ReflectionExtractionRequest,
        extraction: ReflectionExtraction,
    ) -> tuple[int, int]:
        created = 0
        updated = 0
        source_id = f"conversation:{request.message_id}"
        for proposal in extraction.proposals:
            if any(quote not in request.user_text for quote in proposal.evidence):
                raise ReflectionRepositoryError(
                    "reflection evidence must be an exact user-message substring"
                )
            evidence = tuple(
                ReflectionEvidence(source_id, quote) for quote in proposal.evidence
            )
            was_created = self._upsert_candidate(
                kind=proposal.kind,
                content=proposal.content,
                confidence=proposal.confidence,
                temporal_scope=proposal.temporal_scope,
                evidence=evidence,
            )
            created += int(was_created)
            updated += int(not was_created)
        self._record_extraction_status(request.message_id, "completed", None)
        return created, updated

    def record_extraction_failure(self, message_id: int, error: str) -> None:
        self._record_extraction_status(
            message_id,
            "failed",
            error.strip() or "unknown reflection failure",
        )

    def extraction_counts(self, *, user_messages: int) -> dict[str, int]:
        counts = {"completed": 0, "failed": 0, "pending": user_messages}
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM reflection_extractions GROUP BY status
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not count reflection extractions: {error}"
            ) from error
        for row in rows:
            counts[row["status"]] = row["count"]
        counts["pending"] = max(0, user_messages - counts["completed"])
        return counts

    def get(self, candidate_id: str) -> ReflectionCandidate:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM reflection_candidates WHERE id = ?",
                    (candidate_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not read reflection candidate: {error}"
            ) from error
        if row is None:
            raise ReflectionRepositoryError(
                f"reflection candidate not found: {candidate_id}"
            )
        return _candidate_from_row(row)

    def list(
        self, *, status: str | None = None, limit: int | None = None
    ) -> tuple[ReflectionCandidate, ...]:
        if status is not None and status not in REFLECTION_STATUSES:
            raise ReflectionRepositoryError(
                f"unsupported reflection status: {status}"
            )
        if limit is not None and limit < 1:
            raise ReflectionRepositoryError("limit must be at least 1")
        sql = "SELECT * FROM reflection_candidates"
        values: tuple[object, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            values = (status,)
        sql += " ORDER BY created_at, id"
        if limit is not None:
            sql += " LIMIT ?"
            values += (limit,)
        try:
            with self._connect() as connection:
                rows = connection.execute(sql, values).fetchall()
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not list reflection candidates: {error}"
            ) from error
        return tuple(_candidate_from_row(row) for row in rows)

    def approve(
        self,
        candidate_id: str,
        memories: SqliteMemoryRepository,
        *,
        edited_content: str | None = None,
    ) -> ReflectionCandidate:
        candidate = self.get(candidate_id)
        if candidate.status != "candidate":
            raise ReflectionRepositoryError(
                f"cannot approve reflection candidate from {candidate.status}"
            )
        content = (edited_content or candidate.content).strip()
        if not content:
            raise ReflectionRepositoryError("approved memory content is required")
        memory: Memory | None = None
        target_status = "approved"
        if candidate.can_activate_as_memory:
            memory = memories.remember_reviewed(
                kind=candidate.kind,
                content=content,
            )
            target_status = "active"
        self._transition(
            candidate.id,
            status=target_status,
            content=content,
            memory_id=memory.id if memory is not None else None,
        )
        return self.get(candidate.id)

    def link_user_approved_memory(
        self,
        candidate_id: str,
        memory: Memory,
    ) -> ReflectionCandidate:
        """Resolve a candidate to an explicitly approved, retyped memory."""
        candidate = self.get(candidate_id)
        if candidate.status != "candidate":
            raise ReflectionRepositoryError(
                f"cannot link reflection candidate from {candidate.status}"
            )
        if memory.status != "active":
            raise ReflectionRepositoryError(
                "linked reflection memory must already be active"
            )
        self._transition(
            candidate.id,
            status="active",
            content=memory.content,
            memory_id=memory.id,
        )
        return self.get(candidate.id)

    def reject(self, candidate_id: str) -> ReflectionCandidate:
        candidate = self.get(candidate_id)
        if candidate.status != "candidate":
            raise ReflectionRepositoryError(
                f"cannot reject reflection candidate from {candidate.status}"
            )
        self._transition(
            candidate.id,
            status="rejected",
            content=candidate.content,
            memory_id=None,
        )
        return self.get(candidate.id)

    def counts(self) -> dict[str, int]:
        counts = {status: 0 for status in REFLECTION_STATUSES}
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM reflection_candidates GROUP BY status
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not count reflection candidates: {error}"
            ) from error
        for row in rows:
            counts[row["status"]] = row["count"]
        return counts

    def _upsert_candidate(
        self,
        *,
        kind: str,
        content: str,
        confidence: float,
        temporal_scope: str,
        evidence: tuple[ReflectionEvidence, ...],
    ) -> bool:
        fingerprint = _fingerprint(kind, content)
        now = _now()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, confidence, evidence_json
                    FROM reflection_candidates WHERE fingerprint = ?
                    """,
                    (fingerprint,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO reflection_candidates
                        (id, fingerprint, kind, content, confidence, temporal_scope,
                         status, evidence_json, memory_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'candidate', ?, NULL, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            fingerprint,
                            kind,
                            content,
                            confidence,
                            temporal_scope,
                            _evidence_json(evidence),
                            now,
                            now,
                        ),
                    )
                    return True
                merged = _merge_evidence(
                    _evidence_from_json(row["evidence_json"]), evidence
                )
                connection.execute(
                    """
                    UPDATE reflection_candidates
                    SET confidence = ?, evidence_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        max(float(row["confidence"]), confidence),
                        _evidence_json(merged),
                        now,
                        row["id"],
                    ),
                )
                return False
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not save reflection candidate: {error}"
            ) from error

    def _is_processed(self, turn_id: str) -> bool:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT 1 FROM reflection_sources WHERE turn_id = ?",
                    (turn_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not read reflection source: {error}"
            ) from error
        return row is not None

    def _record_extraction_status(
        self, message_id: int, status: str, error: str | None
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO reflection_extractions
                    (message_id, status, error, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        status = excluded.status,
                        error = excluded.error,
                        updated_at = excluded.updated_at
                    """,
                    (message_id, status, error, _now()),
                )
        except sqlite3.Error as sqlite_error:
            raise ReflectionRepositoryError(
                f"could not save reflection extraction status: {sqlite_error}"
            ) from sqlite_error

    def _mark_processed(self, turn_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO reflection_sources VALUES (?, ?)",
                    (turn_id, _now()),
                )
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not mark reflection source: {error}"
            ) from error

    def _transition(
        self,
        candidate_id: str,
        *,
        status: str,
        content: str,
        memory_id: str | None,
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE reflection_candidates
                    SET status = ?, content = ?, memory_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, content, memory_id, _now(), candidate_id),
                )
        except sqlite3.Error as error:
            raise ReflectionRepositoryError(
                f"could not update reflection candidate: {error}"
            ) from error

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS reflection_schema "
                    "(version INTEGER NOT NULL)"
                )
                version = connection.execute(
                    "SELECT version FROM reflection_schema "
                    "ORDER BY version DESC LIMIT 1"
                ).fetchone()
                if version is not None and version["version"] > self._SCHEMA_VERSION:
                    raise ReflectionRepositoryError(
                        f"unsupported reflection schema {version['version']}"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reflection_candidates (
                        id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL UNIQUE,
                        kind TEXT NOT NULL,
                        content TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        temporal_scope TEXT NOT NULL,
                        status TEXT NOT NULL,
                        evidence_json TEXT NOT NULL,
                        memory_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reflection_extractions (
                        message_id INTEGER PRIMARY KEY,
                        status TEXT NOT NULL,
                        error TEXT,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reflection_sources (
                        turn_id TEXT PRIMARY KEY,
                        processed_at TEXT NOT NULL
                    )
                    """
                )
                if version is None:
                    connection.execute(
                        "INSERT INTO reflection_schema VALUES (?)",
                        (self._SCHEMA_VERSION,),
                    )
        except ReflectionRepositoryError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise ReflectionRepositoryError(
                f"could not initialize reflection storage: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


class ConversationReflectionService:
    """Run compact reflection over unprocessed user messages only."""

    def __init__(
        self,
        history: SqliteConversationHistoryReader,
        repository: SqliteReflectionRepository,
        interpreter: ReflectionInterpreter,
    ) -> None:
        self._history = history
        self._repository = repository
        self._interpreter = interpreter

    def process_pending(
        self,
        *,
        limit: int = 10,
        progress: Callable[[int, int], None] | None = None,
    ) -> ReflectionProcessingResult:
        if limit < 1:
            raise ReflectionRepositoryError("reflection limit must be at least 1")
        records = self._history.list_records()
        record_indexes = {record.id: index for index, record in enumerate(records)}
        pending = tuple(
            record
            for record in reversed(records)
            if record.role == "user" and not self._repository.has_extraction(record.id)
        )[:limit]
        completed = 0
        failed = 0
        deferred = 0
        created = 0
        updated = 0
        for index, record in enumerate(pending, start=1):
            record_index = record_indexes[record.id]
            context_start = max(0, record_index - 6)
            recent_context = tuple(
                ConversationMessage(item.role, item.content)
                for item in records[context_start:record_index]
            )
            request = ReflectionExtractionRequest(
                record.id,
                record.content,
                recent_context,
            )
            try:
                extraction = self._interpreter.extract(request)
            except ReflectionInterpreterUnavailableError:
                deferred += 1
            except ReflectionInterpretationError as error:
                self._repository.record_extraction_failure(record.id, str(error))
                failed += 1
            else:
                new, changed = self._repository.record_extraction(
                    request, extraction
                )
                completed += 1
                created += new
                updated += changed
            if progress is not None:
                progress(index, len(pending))
        return ReflectionProcessingResult(
            processed=len(pending),
            completed=completed,
            failed=failed,
            deferred=deferred,
            created_candidates=created,
            updated_candidates=updated,
        )


def _candidate_from_row(row: sqlite3.Row) -> ReflectionCandidate:
    kind = row["kind"]
    temporal_scope = row["temporal_scope"]
    status = row["status"]
    if kind not in MEMORY_KINDS:
        raise ReflectionRepositoryError(f"stored candidate has invalid kind: {kind}")
    if temporal_scope not in TEMPORAL_SCOPES:
        raise ReflectionRepositoryError(
            f"stored candidate has invalid temporal scope: {temporal_scope}"
        )
    if status not in REFLECTION_STATUSES:
        raise ReflectionRepositoryError(
            f"stored candidate has invalid status: {status}"
        )
    confidence = float(row["confidence"])
    if not 0 <= confidence <= 1:
        raise ReflectionRepositoryError("stored candidate confidence is invalid")
    return ReflectionCandidate(
        id=row["id"],
        kind=kind,
        content=row["content"],
        confidence=confidence,
        temporal_scope=temporal_scope,
        status=status,
        evidence=_evidence_from_json(row["evidence_json"]),
        memory_id=row["memory_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _evidence_json(evidence: tuple[ReflectionEvidence, ...]) -> str:
    return json.dumps(
        [asdict(item) for item in evidence],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _evidence_from_json(raw: Any) -> tuple[ReflectionEvidence, ...]:
    if not isinstance(raw, str):
        raise ReflectionRepositoryError("stored reflection evidence is not JSON text")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ReflectionRepositoryError("stored reflection evidence is invalid JSON") from error
    if not isinstance(decoded, list):
        raise ReflectionRepositoryError("stored reflection evidence must be a list")
    evidence: list[ReflectionEvidence] = []
    for item in decoded:
        if not isinstance(item, dict) or set(item) != {"turn_id", "quote"}:
            raise ReflectionRepositoryError("stored reflection evidence fields are invalid")
        turn_id = item["turn_id"]
        quote = item["quote"]
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ReflectionRepositoryError("stored evidence turn id is invalid")
        if not isinstance(quote, str) or not quote.strip():
            raise ReflectionRepositoryError("stored evidence quote is invalid")
        evidence.append(ReflectionEvidence(turn_id, quote))
    return tuple(evidence)


def _merge_evidence(
    current: tuple[ReflectionEvidence, ...],
    incoming: tuple[ReflectionEvidence, ...],
) -> tuple[ReflectionEvidence, ...]:
    merged: list[ReflectionEvidence] = []
    seen: set[tuple[str, str]] = set()
    for evidence in current + incoming:
        key = (evidence.turn_id, evidence.quote)
        if key not in seen:
            seen.add(key)
            merged.append(evidence)
    return tuple(merged)


def _fingerprint(kind: str, content: str) -> str:
    normalized = " ".join(content.lower().split())
    return hashlib.sha256(f"{kind}\0{normalized}".encode()).hexdigest()


def _second(value: str) -> str:
    normalized = value.strip().replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return normalized[:19]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed.isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now(UTC).isoformat()
