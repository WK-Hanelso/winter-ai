"""Evidence-backed turn interpretation stored in non-operative Shadow Mode."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol, cast
from uuid import uuid4

from companion.contracts import ConversationMessage

SCHEMA_VERSION = 1
TEMPORAL_SCOPES = frozenset(
    {"turn", "session", "episodic", "candidate_stable", "stable", "unknown"}
)
MEMORY_KINDS = frozenset(
    {
        "semantic",
        "episodic",
        "preference",
        "decision",
        "project",
        "procedural",
        "relationship",
        "improvement",
    }
)
EVENT_STATUSES = frozenset({"pending", "completed", "failed"})


class TurnInterpretationError(ValueError):
    """Raised when an interpreter result violates the observable contract."""


class TurnInterpreterUnavailableError(TurnInterpretationError):
    """A transient local-model failure that should stay pending for retry."""


class TurnUnderstandingRepositoryError(RuntimeError):
    """Raised when the Shadow event store cannot be read or written."""


@dataclass(frozen=True)
class EvidenceClaim:
    content: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ScoredHypothesis:
    label: str
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class MemoryProposal:
    kind: str
    content: str
    confidence: float
    temporal_scope: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ResponseContract:
    must: tuple[str, ...]
    avoid: tuple[str, ...]


@dataclass(frozen=True)
class TurnUnderstanding:
    schema_version: int
    literal_meaning: str
    observations: tuple[EvidenceClaim, ...]
    intent_hypotheses: tuple[ScoredHypothesis, ...]
    affect: tuple[ScoredHypothesis, ...]
    conversational_need: str
    temporal_scope: str
    response_contract: ResponseContract
    memory_proposals: tuple[MemoryProposal, ...]
    open_loops: tuple[ScoredHypothesis, ...]
    project_signals: tuple[ScoredHypothesis, ...]
    improvement_signals: tuple[ScoredHypothesis, ...]
    uncertainties: tuple[str, ...]

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        source_text: str,
        context_texts: tuple[str, ...] = (),
    ) -> TurnUnderstanding:
        data = _mapping(payload, "TurnUnderstanding")
        expected = {
            "schema_version",
            "literal_meaning",
            "observations",
            "intent_hypotheses",
            "affect",
            "conversational_need",
            "temporal_scope",
            "response_contract",
            "memory_proposals",
            "open_loops",
            "project_signals",
            "improvement_signals",
            "uncertainties",
        }
        if set(data) != expected:
            missing = sorted(expected - set(data))
            extra = sorted(set(data) - expected)
            raise TurnInterpretationError(
                f"TurnUnderstanding fields mismatch: missing={missing}, extra={extra}"
            )
        version = data["schema_version"]
        if version != SCHEMA_VERSION:
            raise TurnInterpretationError(
                f"unsupported TurnUnderstanding schema_version: {version}"
            )
        evidence_corpus = (source_text,) + context_texts
        temporal_scope = _string(data["temporal_scope"], "temporal_scope")
        if temporal_scope not in TEMPORAL_SCOPES:
            raise TurnInterpretationError(
                f"unsupported temporal_scope: {temporal_scope}"
            )
        response_data = _mapping(data["response_contract"], "response_contract")
        if set(response_data) != {"must", "avoid"}:
            raise TurnInterpretationError(
                "response_contract must contain only must and avoid"
            )
        return cls(
            schema_version=SCHEMA_VERSION,
            literal_meaning=_string(data["literal_meaning"], "literal_meaning"),
            observations=tuple(
                _evidence_claim(item, evidence_corpus, "observations")
                for item in _sequence(data["observations"], "observations")
            ),
            intent_hypotheses=tuple(
                _scored(item, evidence_corpus, "intent_hypotheses")
                for item in _sequence(
                    data["intent_hypotheses"], "intent_hypotheses"
                )
            ),
            affect=tuple(
                _scored(item, evidence_corpus, "affect")
                for item in _sequence(data["affect"], "affect")
            ),
            conversational_need=_string(
                data["conversational_need"], "conversational_need"
            ),
            temporal_scope=temporal_scope,
            response_contract=ResponseContract(
                must=_strings(response_data["must"], "response_contract.must"),
                avoid=_strings(response_data["avoid"], "response_contract.avoid"),
            ),
            memory_proposals=tuple(
                _memory_proposal(item, evidence_corpus)
                for item in _sequence(data["memory_proposals"], "memory_proposals")
            ),
            open_loops=tuple(
                _scored(item, evidence_corpus, "open_loops")
                for item in _sequence(data["open_loops"], "open_loops")
            ),
            project_signals=tuple(
                _scored(item, evidence_corpus, "project_signals")
                for item in _sequence(data["project_signals"], "project_signals")
            ),
            improvement_signals=tuple(
                _scored(item, evidence_corpus, "improvement_signals")
                for item in _sequence(
                    data["improvement_signals"], "improvement_signals"
                )
            ),
            uncertainties=_strings(data["uncertainties"], "uncertainties"),
        )

    def to_dict(self) -> dict[str, Any]:
        # JSON round-trip converts nested tuples to arrays and returns a payload
        # with the exact shape the local interpreter contract expects.
        return cast(
            dict[str, Any], json.loads(json.dumps(asdict(self), ensure_ascii=False))
        )


@dataclass(frozen=True)
class TurnInterpretationRequest:
    user_text: str
    context: tuple[ConversationMessage, ...] = ()

    def __post_init__(self) -> None:
        if not self.user_text.strip():
            raise TurnInterpretationError("user_text is required")
        if any(message.role not in {"user", "assistant"} for message in self.context):
            raise TurnInterpretationError(
                "TurnUnderstanding context accepts only user and assistant messages"
            )


class TurnInterpreter(Protocol):
    def interpret(self, request: TurnInterpretationRequest) -> TurnUnderstanding: ...


@dataclass(frozen=True)
class ShadowTurnEvent:
    id: str
    source: str
    request: TurnInterpretationRequest
    status: str
    understanding: TurnUnderstanding | None
    error: str | None
    created_at: str
    updated_at: str


class FakeTurnInterpreter:
    """Explicit offline fake: it records the utterance and infers nothing."""

    def interpret(self, request: TurnInterpretationRequest) -> TurnUnderstanding:
        return TurnUnderstanding.from_dict(
            {
                "schema_version": SCHEMA_VERSION,
                "literal_meaning": request.user_text,
                "observations": [
                    {
                        "content": "The user produced this exact utterance.",
                        "evidence": [request.user_text],
                    }
                ],
                "intent_hypotheses": [],
                "affect": [],
                "conversational_need": "unknown",
                "temporal_scope": "unknown",
                "response_contract": {"must": [], "avoid": []},
                "memory_proposals": [],
                "open_loops": [],
                "project_signals": [],
                "improvement_signals": [],
                "uncertainties": (
                    "The deterministic fake interpreter does not infer intent.",
                ),
            },
            source_text=request.user_text,
            context_texts=tuple(message.content for message in request.context),
        )


class SqliteTurnUnderstandingRepository:
    """Persists raw turns before any interpreter can affect product behaviour."""

    _SCHEMA_VERSION = 2

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def enqueue(
        self, request: TurnInterpretationRequest, *, source: str
    ) -> ShadowTurnEvent:
        if not source.strip():
            raise TurnUnderstandingRepositoryError("turn source is required")
        now = _now()
        event = ShadowTurnEvent(
            id=str(uuid4()),
            source=source.strip(),
            request=request,
            status="pending",
            understanding=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO turn_understanding_events
                    (id, source, user_text, context_json, status,
                     understanding_json, error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _event_values(event),
                )
        except sqlite3.Error as error:
            raise TurnUnderstandingRepositoryError(
                f"could not enqueue Shadow turn: {error}"
            ) from error
        return event

    def enqueue_recovered(
        self,
        request: TurnInterpretationRequest,
        *,
        conversation_message_id: int,
        conversation_created_at: str,
    ) -> ShadowTurnEvent:
        """Queue one historical user message exactly once.

        The source link is stored in the same transaction as the Shadow event,
        so rerunning recovery cannot duplicate an older conversation turn.
        """
        if conversation_message_id < 1 or not conversation_created_at.strip():
            raise TurnUnderstandingRepositoryError(
                "historical message id and created_at are required"
            )
        existing = self.recovered_event_for(conversation_message_id)
        if existing is not None:
            return existing
        now = _now()
        event = ShadowTurnEvent(
            id=str(uuid4()),
            source="history",
            request=request,
            status="pending",
            understanding=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO turn_understanding_events
                    (id, source, user_text, context_json, status,
                     understanding_json, error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _event_values(event),
                )
                connection.execute(
                    """
                    INSERT INTO turn_recovery_sources
                    (conversation_message_id, event_id, conversation_created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        conversation_message_id,
                        event.id,
                        conversation_created_at.strip(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            existing = self.recovered_event_for(conversation_message_id)
            if existing is not None:
                return existing
            raise TurnUnderstandingRepositoryError(
                "historical turn recovery conflicted with existing state"
            ) from error
        except sqlite3.Error as error:
            raise TurnUnderstandingRepositoryError(
                f"could not enqueue historical turn: {error}"
            ) from error
        return event

    def link_recovered_message(
        self,
        *,
        conversation_message_id: int,
        event_id: str,
        conversation_created_at: str,
    ) -> None:
        """Attach a pre-existing Shadow event to its exact conversation row."""
        self.get(event_id)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO turn_recovery_sources
                    (conversation_message_id, event_id, conversation_created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        conversation_message_id,
                        event_id,
                        conversation_created_at.strip(),
                    ),
                )
        except sqlite3.Error as error:
            raise TurnUnderstandingRepositoryError(
                f"could not link historical turn: {error}"
            ) from error

    def recovered_event_for(
        self, conversation_message_id: int
    ) -> ShadowTurnEvent | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT event_id FROM turn_recovery_sources
                    WHERE conversation_message_id = ?
                    """,
                    (conversation_message_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TurnUnderstandingRepositoryError(
                f"could not read historical turn link: {error}"
            ) from error
        return self.get(row["event_id"]) if row is not None else None

    def complete(
        self, event_id: str, understanding: TurnUnderstanding
    ) -> ShadowTurnEvent:
        current = self.get(event_id)
        if current.status != "pending":
            raise TurnUnderstandingRepositoryError(
                f"cannot complete event {event_id} from {current.status}"
            )
        self._update(
            event_id,
            status="completed",
            understanding_json=json.dumps(
                understanding.to_dict(), ensure_ascii=False, separators=(",", ":")
            ),
            error=None,
        )
        return self.get(event_id)

    def fail(self, event_id: str, error: str) -> ShadowTurnEvent:
        current = self.get(event_id)
        if current.status != "pending":
            raise TurnUnderstandingRepositoryError(
                f"cannot fail event {event_id} from {current.status}"
            )
        detail = error.strip() or "unknown interpreter failure"
        self._update(
            event_id,
            status="failed",
            understanding_json=None,
            error=detail,
        )
        return self.get(event_id)

    def get(self, event_id: str) -> ShadowTurnEvent:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM turn_understanding_events WHERE id = ?",
                    (event_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TurnUnderstandingRepositoryError(
                f"could not read Shadow turn: {error}"
            ) from error
        if row is None:
            raise TurnUnderstandingRepositoryError(
                f"Shadow turn not found: {event_id}"
            )
        return _event_from_row(row)

    def list(
        self, *, status: str | None = None, limit: int | None = None
    ) -> tuple[ShadowTurnEvent, ...]:
        if status is not None and status not in EVENT_STATUSES:
            raise TurnUnderstandingRepositoryError(
                f"unsupported Shadow turn status: {status}"
            )
        if limit is not None and limit < 1:
            raise TurnUnderstandingRepositoryError("limit must be at least 1")
        sql = "SELECT * FROM turn_understanding_events"
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
            raise TurnUnderstandingRepositoryError(
                f"could not list Shadow turns: {error}"
            ) from error
        return tuple(_event_from_row(row) for row in rows)

    def _update(
        self,
        event_id: str,
        *,
        status: str,
        understanding_json: str | None,
        error: str | None,
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE turn_understanding_events
                    SET status = ?, understanding_json = ?, error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, understanding_json, error, _now(), event_id),
                )
        except sqlite3.Error as sqlite_error:
            raise TurnUnderstandingRepositoryError(
                f"could not update Shadow turn: {sqlite_error}"
            ) from sqlite_error

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS turn_understanding_schema "
                    "(version INTEGER NOT NULL)"
                )
                version = connection.execute(
                    "SELECT version FROM turn_understanding_schema "
                    "ORDER BY version DESC LIMIT 1"
                ).fetchone()
                if version is not None and version["version"] > self._SCHEMA_VERSION:
                    raise TurnUnderstandingRepositoryError(
                        f"unsupported schema version {version['version']}"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS turn_understanding_events (
                        id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        user_text TEXT NOT NULL,
                        context_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        understanding_json TEXT,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS turn_recovery_sources (
                        conversation_message_id INTEGER PRIMARY KEY,
                        event_id TEXT NOT NULL UNIQUE,
                        conversation_created_at TEXT NOT NULL,
                        FOREIGN KEY(event_id) REFERENCES turn_understanding_events(id)
                    )
                    """
                )
                if version is None:
                    connection.execute(
                        "INSERT INTO turn_understanding_schema VALUES (?)",
                        (self._SCHEMA_VERSION,),
                    )
                elif version["version"] < self._SCHEMA_VERSION:
                    connection.execute(
                        "UPDATE turn_understanding_schema SET version = ?",
                        (self._SCHEMA_VERSION,),
                    )
        except TurnUnderstandingRepositoryError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise TurnUnderstandingRepositoryError(
                f"could not initialize Shadow turn storage: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


@dataclass(frozen=True)
class ProcessingResult:
    processed: int
    completed: int
    failed: int
    deferred: int


class ShadowAnalysisService:
    def __init__(
        self,
        repository: SqliteTurnUnderstandingRepository,
        interpreter: TurnInterpreter,
    ) -> None:
        self._repository = repository
        self._interpreter = interpreter

    def process_pending(self, *, limit: int = 10) -> ProcessingResult:
        pending = self._repository.list(status="pending", limit=limit)
        completed = 0
        failed = 0
        deferred = 0
        for event in pending:
            try:
                understanding = self._interpreter.interpret(event.request)
            except TurnInterpreterUnavailableError:
                deferred += 1
            except TurnInterpretationError as error:
                self._repository.fail(event.id, str(error))
                failed += 1
            else:
                self._repository.complete(event.id, understanding)
                completed += 1
        return ProcessingResult(len(pending), completed, failed, deferred)


def _evidence_claim(
    payload: object, evidence_corpus: tuple[str, ...], label: str
) -> EvidenceClaim:
    data = _mapping(payload, label)
    if set(data) != {"content", "evidence"}:
        raise TurnInterpretationError(
            f"{label} entries must contain only content and evidence"
        )
    return EvidenceClaim(
        _string(data["content"], f"{label}.content"),
        _evidence(data["evidence"], evidence_corpus, f"{label}.evidence"),
    )


def _scored(
    payload: object, evidence_corpus: tuple[str, ...], label: str
) -> ScoredHypothesis:
    data = _mapping(payload, label)
    if set(data) != {"label", "confidence", "evidence"}:
        raise TurnInterpretationError(
            f"{label} entries must contain label, confidence and evidence"
        )
    return ScoredHypothesis(
        _string(data["label"], f"{label}.label"),
        _confidence(data["confidence"], f"{label}.confidence"),
        _evidence(data["evidence"], evidence_corpus, f"{label}.evidence"),
    )


def _memory_proposal(
    payload: object, evidence_corpus: tuple[str, ...]
) -> MemoryProposal:
    data = _mapping(payload, "memory_proposals")
    expected = {"kind", "content", "confidence", "temporal_scope", "evidence"}
    if set(data) != expected:
        raise TurnInterpretationError(
            "memory_proposals entries require kind, content, confidence, "
            "temporal_scope and evidence"
        )
    kind = _string(data["kind"], "memory_proposals.kind")
    if kind not in MEMORY_KINDS:
        raise TurnInterpretationError(f"unsupported memory proposal kind: {kind}")
    temporal_scope = _string(
        data["temporal_scope"], "memory_proposals.temporal_scope"
    )
    if temporal_scope not in TEMPORAL_SCOPES:
        raise TurnInterpretationError(
            f"unsupported memory proposal temporal_scope: {temporal_scope}"
        )
    return MemoryProposal(
        kind,
        _string(data["content"], "memory_proposals.content"),
        _confidence(data["confidence"], "memory_proposals.confidence"),
        temporal_scope,
        _evidence(
            data["evidence"], evidence_corpus, "memory_proposals.evidence"
        ),
    )


def _evidence(
    value: object, evidence_corpus: tuple[str, ...], label: str
) -> tuple[str, ...]:
    evidence = _strings(value, label)
    if not evidence:
        raise TurnInterpretationError(f"{label} must contain at least one quote")
    for quote in evidence:
        if not any(quote in text for text in evidence_corpus):
            raise TurnInterpretationError(
                f"{label} quote must be an exact substring of the source context: {quote!r}"
            )
    return evidence


def _confidence(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TurnInterpretationError(f"{label} confidence must be a number")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise TurnInterpretationError(f"{label} confidence must be between 0 and 1")
    return confidence


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise TurnInterpretationError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise TurnInterpretationError(f"{label} must be an array")
    return tuple(value)


def _strings(value: object, label: str) -> tuple[str, ...]:
    items = _sequence(value, label)
    return tuple(_string(item, label) for item in items)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TurnInterpretationError(f"{label} must be a non-empty string")
    return value.strip()


def _event_values(event: ShadowTurnEvent) -> tuple[object, ...]:
    context = [asdict(message) for message in event.request.context]
    return (
        event.id,
        event.source,
        event.request.user_text,
        json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        event.status,
        None,
        event.error,
        event.created_at,
        event.updated_at,
    )


def _event_from_row(row: sqlite3.Row) -> ShadowTurnEvent:
    try:
        context_payload = json.loads(row["context_json"])
        context = tuple(
            ConversationMessage(role=item["role"], content=item["content"])
            for item in context_payload
        )
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise TurnUnderstandingRepositoryError(
            f"Shadow turn context is invalid JSON: {error}"
        ) from error
    request = TurnInterpretationRequest(row["user_text"], context)
    understanding = None
    if row["understanding_json"] is not None:
        try:
            payload = json.loads(row["understanding_json"])
        except json.JSONDecodeError as error:
            raise TurnUnderstandingRepositoryError(
                f"Shadow understanding is invalid JSON: {error}"
            ) from error
        try:
            understanding = TurnUnderstanding.from_dict(
                payload,
                source_text=request.user_text,
                context_texts=tuple(message.content for message in context),
            )
        except TurnInterpretationError as error:
            raise TurnUnderstandingRepositoryError(
                f"stored Shadow understanding violates schema: {error}"
            ) from error
    status = row["status"]
    if status not in EVENT_STATUSES:
        raise TurnUnderstandingRepositoryError(
            f"stored Shadow turn has unsupported status: {status}"
        )
    return ShadowTurnEvent(
        id=row["id"],
        source=row["source"],
        request=request,
        status=status,
        understanding=understanding,
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
