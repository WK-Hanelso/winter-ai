"""Post-session fact extraction and atomic, resumable storage in the memory DB."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
import sqlite3
from typing import Protocol
from uuid import uuid4

from companion.contracts import ConversationMessage
from companion.memory import (
    _CURRENT_STATE_TOPIC,
    _TEMPORAL_SCOPES,
    _UNCERTAIN_WORDING,
    Memory,
    SqliteMemoryRepository,
    _from_row,
    _now,
    _terms,
    _validate_provenance,
    _values,
)
from companion.reflection import SqliteConversationHistoryReader

VERSION = 1
FACT_KINDS = frozenset({
    "semantic", "preference", "project", "decision", "procedural", "relationship", "episodic",
})


class FactExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class Fact:
    kind: str
    content: str
    confidence: float
    temporal_scope: str
    evidence: str


@dataclass(frozen=True)
class FactExtractionRequest:
    message_id: int
    user_text: str
    recent_context: tuple[ConversationMessage, ...] = ()
    active_memories: tuple[str, ...] = ()
    current_state_topics: tuple[str, ...] = ()


class FactExtractor(Protocol):
    def extract(self, request: FactExtractionRequest) -> tuple[Fact, ...]: ...

    def verify(self, request: FactExtractionRequest,
               facts: tuple[Fact, ...]) -> tuple[bool, ...]: ...


class FakeFactExtractor:
    def extract(self, request: FactExtractionRequest) -> tuple[Fact, ...]:
        return ()

    def verify(self, request: FactExtractionRequest, facts: tuple[Fact, ...]) -> tuple[bool, ...]:
        return (False,) * len(facts)


@dataclass(frozen=True)
class FactProcessingResult:
    processed: int = 0
    stored: int = 0
    rejected: int = 0
    failed: int = 0


def validate_fact(fact: Fact) -> None:
    topic = fact.kind.removeprefix("current_state:")
    if fact.kind not in FACT_KINDS and not (
        fact.kind.startswith("current_state:") and _CURRENT_STATE_TOPIC.fullmatch(topic)
    ):
        raise FactExtractionError("invalid fact kind")
    if not fact.content.strip() or not fact.evidence.strip():
        raise FactExtractionError("fact content and evidence are required")
    if (isinstance(fact.confidence, bool) or not isinstance(fact.confidence, (float, int))
            or not 0 <= fact.confidence <= 1):
        raise FactExtractionError("fact confidence must be between 0 and 1")
    if fact.temporal_scope not in _TEMPORAL_SCOPES:
        raise FactExtractionError("invalid fact temporal scope")


def _duplicate(fact: Fact, memory: Memory) -> bool:
    if fact.kind != memory.kind:
        return False
    if fact.content == memory.content:
        return True
    left, right = _terms(fact.content), _terms(memory.content)
    union = left | right
    return bool(union) and len(left & right) / len(union) >= 0.8


class SqliteFactExtractionRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.memories = SqliteMemoryRepository(path)
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS fact_extractions (
                    message_id INTEGER PRIMARY KEY, version INTEGER, status TEXT,
                    facts INTEGER, stored INTEGER, rejected INTEGER, error TEXT, created_at TEXT
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def completed_ids(self) -> set[int]:
        with closing(self._connect()) as connection:
            return {int(row[0]) for row in connection.execute(
                "SELECT message_id FROM fact_extractions "
                "WHERE version = ? AND status = 'completed'",
                (VERSION,),
            )}

    def record_failure(self, message_id: int, error: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                INSERT INTO fact_extractions VALUES (?, ?, 'failed', 0, 0, 0, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET version = excluded.version,
                    status = 'failed', facts = 0, stored = 0, rejected = 0,
                    error = excluded.error, created_at = excluded.created_at
                WHERE fact_extractions.status != 'completed' OR fact_extractions.version != ?
            """, (message_id, VERSION, error, _now(), VERSION))

    def store(self, request: FactExtractionRequest, facts: tuple[Fact, ...], *,
              total: int, rejected: int) -> int:
        """Commit all accepted facts and completion together, including state History."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM fact_extractions WHERE message_id = ? AND version = ? "
                "AND status = 'completed'", (request.message_id, VERSION),
            ).fetchone():
                return 0
            stored = 0
            for fact in facts:
                validate_fact(fact)
                if fact.evidence not in request.user_text:
                    raise FactExtractionError("fact evidence must occur in user text")
                _validate_provenance(f"conversation:{request.message_id}", fact.temporal_scope)
                active = tuple(_from_row(row) for row in connection.execute(
                    "SELECT * FROM memories WHERE status = 'active'",
                ))
                if any(_duplicate(fact, memory) for memory in active):
                    continue
                evidence_ref = f"conversation:{request.message_id}"
                if fact.kind.startswith("current_state:") and fact.confidence >= 0.6:
                    self.memories.remember_current_state(
                        topic=fact.kind.removeprefix("current_state:"), content=fact.content,
                        source="extracted", confidence=fact.confidence, importance=5,
                        evidence_ref=evidence_ref, temporal_scope=fact.temporal_scope,
                        connection=connection,
                    )
                else:
                    now = _now()
                    memory = Memory(
                        str(uuid4()), fact.kind, fact.content, 5, fact.confidence,
                        "active" if fact.confidence >= 0.6 else "candidate", "extracted",
                        now, now, None, None, evidence_ref, fact.temporal_scope,
                    )
                    connection.execute(
                        "INSERT INTO memories (id, kind, content, importance, confidence, status, "
                        "source, created_at, updated_at, last_accessed_at, supersedes, "
                        "evidence_ref, "
                        "temporal_scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        _values(memory),
                    )
                stored += 1
            connection.execute("""
                INSERT INTO fact_extractions VALUES (?, ?, 'completed', ?, ?, ?, NULL, ?)
                ON CONFLICT(message_id) DO UPDATE SET version = excluded.version,
                    status = 'completed', facts = excluded.facts, stored = excluded.stored,
                    rejected = excluded.rejected, error = NULL, created_at = excluded.created_at
            """, (request.message_id, VERSION, total, stored, rejected, _now()))
            return stored


class FactExtractionService:
    def __init__(self, history: SqliteConversationHistoryReader,
                 repository: SqliteFactExtractionRepository, extractor: FactExtractor) -> None:
        self._history = history
        self._repository = repository
        self._extractor = extractor

    def process_pending(self, *, limit: int | None = 40,
                        progress: Callable[[int, int], None] | None = None) -> FactProcessingResult:
        if limit is not None and limit < 1:
            raise ValueError("fact extraction limit must be at least 1")
        records = self._history.list_records()
        completed = self._repository.completed_ids()
        pending = [(i, record) for i, record in enumerate(records)
                   if record.role == "user" and record.id not in completed][:limit]
        stored = rejected = failed = 0
        for index, (position, record) in enumerate(pending, start=1):
            try:
                active = sorted(self._repository.memories.list_active(),
                                key=lambda m: (m.updated_at, m.id), reverse=True)
                request = FactExtractionRequest(
                    record.id, record.content,
                    tuple(ConversationMessage(m.role, m.content)
                          for m in records[max(0, position - 6):position]),
                    tuple(m.content for m in active[:30]),
                    tuple(sorted({m.kind.removeprefix("current_state:") for m in active
                                  if m.kind.startswith("current_state:")})),
                )
                # Recheck before calling the model if another run completed this message.
                if record.id not in self._repository.completed_ids():
                    raw = self._extractor.extract(request)
                    if len(raw) > 5:
                        raise FactExtractionError("at most five facts are allowed")
                    grounded = []
                    for fact in raw:
                        validate_fact(fact)
                        if fact.evidence not in request.user_text:
                            continue
                        if _UNCERTAIN_WORDING.search(fact.evidence):
                            fact = replace(fact, confidence=min(0.8, fact.confidence))
                        grounded.append(fact)
                    facts = tuple(grounded)
                    accept = self._extractor.verify(request, facts) if facts else ()
                    if len(accept) != len(facts) or any(type(a) is not bool for a in accept):
                        raise FactExtractionError("verification must return one boolean per fact")
                    accepted = tuple(f for f, a in zip(facts, accept, strict=True) if a)
                    count_rejected = len(raw) - len(accepted)
                    stored += self._repository.store(
                        request, accepted, total=len(raw), rejected=count_rejected,
                    )
                    rejected += count_rejected
            except Exception as error:  # noqa: BLE001 - persist failure and continue backfill
                # Model/provider exceptions may contain private payloads; never copy them to logs.
                detail = (str(error) if isinstance(error, FactExtractionError)
                          else type(error).__name__)
                self._repository.record_failure(record.id, detail)
                failed += 1
            if progress is not None:
                progress(index, len(pending))
        return FactProcessingResult(len(pending), stored, rejected, failed)
