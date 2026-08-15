"""Evidence-backed next-turn outcomes kept outside operative companion state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol, cast
from uuid import uuid4

from companion.turn_understanding import (
    SqliteTurnUnderstandingRepository,
    TurnUnderstanding,
)

SCHEMA_VERSION = 1
OUTCOMES = frozenset(
    {"accepted", "corrected", "rejected", "continued", "abandoned", "unclear"}
)
INTENT_MATCHES = frozenset({"confirmed", "contradicted", "partial", "unclear"})
USEFULNESS = frozenset({"helpful", "unhelpful", "mixed", "unclear"})
AFFECT_SHIFTS = frozenset({"improved", "worsened", "unchanged", "unclear"})
EVENT_STATUSES = frozenset({"pending", "completed", "failed"})
MEMORY_RELATIONS = frozenset({"confirmed", "contradicted", "none", "unclear"})
IMPROVEMENT_REVIEWS = frozenset({"confirmed", "none", "unclear"})


class OutcomeInterpretationError(ValueError):
    """Raised when an outcome violates its observable evidence contract."""


class OutcomeInterpreterUnavailableError(OutcomeInterpretationError):
    """A transient local-model failure that should stay pending for retry."""


class OutcomeRepositoryError(RuntimeError):
    """Raised when Shadow outcome state cannot be persisted."""


@dataclass(frozen=True)
class OutcomeSignal:
    content: str
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class OutcomeAssessment:
    schema_version: int
    outcome: str
    confidence: float
    evidence: tuple[str, ...]
    intent_match: str
    response_usefulness: str
    affect_shift: str
    memory_confirmation: tuple[OutcomeSignal, ...]
    memory_contradiction: tuple[OutcomeSignal, ...]
    improvement_confirmation: tuple[OutcomeSignal, ...]
    uncertainties: tuple[str, ...]

    @classmethod
    def from_dict(
        cls, payload: object, *, evidence_text: str
    ) -> OutcomeAssessment:
        data = _mapping(payload, "OutcomeAssessment")
        expected = {
            "schema_version",
            "outcome",
            "confidence",
            "evidence",
            "intent_match",
            "response_usefulness",
            "affect_shift",
            "memory_confirmation",
            "memory_contradiction",
            "improvement_confirmation",
            "uncertainties",
        }
        if set(data) != expected:
            missing = sorted(expected - set(data))
            extra = sorted(set(data) - expected)
            raise OutcomeInterpretationError(
                f"OutcomeAssessment fields mismatch: missing={missing}, extra={extra}"
            )
        if data["schema_version"] != SCHEMA_VERSION:
            raise OutcomeInterpretationError(
                f"unsupported OutcomeAssessment schema_version: {data['schema_version']}"
            )
        outcome = _enum(data["outcome"], OUTCOMES, "outcome")
        intent_match = _enum(
            data["intent_match"], INTENT_MATCHES, "intent_match"
        )
        usefulness = _enum(
            data["response_usefulness"], USEFULNESS, "response_usefulness"
        )
        affect_shift = _enum(
            data["affect_shift"], AFFECT_SHIFTS, "affect_shift"
        )
        return cls(
            schema_version=SCHEMA_VERSION,
            outcome=outcome,
            confidence=_confidence(data["confidence"], "confidence"),
            evidence=_evidence(data["evidence"], evidence_text, "evidence"),
            intent_match=intent_match,
            response_usefulness=usefulness,
            affect_shift=affect_shift,
            memory_confirmation=_signals(
                data["memory_confirmation"], evidence_text, "memory_confirmation"
            ),
            memory_contradiction=_signals(
                data["memory_contradiction"], evidence_text, "memory_contradiction"
            ),
            improvement_confirmation=_signals(
                data["improvement_confirmation"],
                evidence_text,
                "improvement_confirmation",
            ),
            uncertainties=_strings(data["uncertainties"], "uncertainties"),
        )

    def to_dict(self) -> dict[str, Any]:
        return cast(
            dict[str, Any], json.loads(json.dumps(asdict(self), ensure_ascii=False))
        )


@dataclass(frozen=True)
class OutcomeMemoryClaim:
    id: str
    kind: str
    content: str

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.kind.strip() or not self.content.strip():
            raise OutcomeInterpretationError(
                "memory claim id, kind and content are required"
            )


@dataclass(frozen=True)
class OutcomeInterpretationRequest:
    prior_user_text: str
    prior_understanding: TurnUnderstanding
    assistant_response: str
    next_user_text: str
    memory_claims: tuple[OutcomeMemoryClaim, ...] = ()

    def __post_init__(self) -> None:
        for label, value in (
            ("prior_user_text", self.prior_user_text),
            ("assistant_response", self.assistant_response),
            ("next_user_text", self.next_user_text),
        ):
            if not value.strip():
                raise OutcomeInterpretationError(f"{label} is required")


class OutcomeInterpreter(Protocol):
    def evaluate(self, request: OutcomeInterpretationRequest) -> OutcomeAssessment: ...


class FakeOutcomeInterpreter:
    """Explicit offline fake that never infers acceptance from a next turn."""

    def evaluate(self, request: OutcomeInterpretationRequest) -> OutcomeAssessment:
        return OutcomeAssessment.from_dict(
            {
                "schema_version": SCHEMA_VERSION,
                "outcome": "unclear",
                "confidence": 0.0,
                "evidence": [request.next_user_text],
                "intent_match": "unclear",
                "response_usefulness": "unclear",
                "affect_shift": "unclear",
                "memory_confirmation": [],
                "memory_contradiction": [],
                "improvement_confirmation": [],
                "uncertainties": [
                    "The deterministic fake evaluator does not infer outcomes."
                ],
            },
            evidence_text=request.next_user_text,
        )


@dataclass(frozen=True)
class ShadowOutcomeEvent:
    id: str
    prior_turn_id: str
    next_turn_id: str
    prior_user_text: str
    assistant_response: str
    next_user_text: str
    prior_memory_claims: tuple[OutcomeMemoryClaim, ...]
    status: str
    assessment: OutcomeAssessment | None
    error: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ReviewedMemoryRelation:
    claim_id: str
    relation: str

    def __post_init__(self) -> None:
        if not self.claim_id.strip():
            raise OutcomeInterpretationError("reviewed memory claim id is required")
        if self.relation not in MEMORY_RELATIONS:
            raise OutcomeInterpretationError(
                f"unsupported reviewed memory relation: {self.relation}"
            )


@dataclass(frozen=True)
class OutcomeReview:
    id: str
    event_id: str
    reviewer: str
    outcome: str
    intent_match: str
    response_usefulness: str
    affect_shift: str
    memory_relations: tuple[ReviewedMemoryRelation, ...]
    improvement_confirmation: str
    created_at: str

    def __post_init__(self) -> None:
        required = {
            "id": self.id,
            "event_id": self.event_id,
            "reviewer": self.reviewer,
            "created_at": self.created_at,
        }
        for label, value in required.items():
            if not value.strip():
                raise OutcomeInterpretationError(f"review {label} is required")
        _enum(self.outcome, OUTCOMES, "review outcome")
        _enum(self.intent_match, INTENT_MATCHES, "review intent_match")
        _enum(self.response_usefulness, USEFULNESS, "review response_usefulness")
        _enum(self.affect_shift, AFFECT_SHIFTS, "review affect_shift")
        _enum(
            self.improvement_confirmation,
            IMPROVEMENT_REVIEWS,
            "review improvement_confirmation",
        )
        ids = [relation.claim_id for relation in self.memory_relations]
        if len(ids) != len(set(ids)):
            raise OutcomeInterpretationError(
                "reviewed memory claim ids must be unique"
            )


class SqliteOutcomeRepository:
    """Links one responded companion turn to the user's immediately next turn."""

    _SCHEMA_VERSION = 3

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def observe_turn(
        self,
        turn_id: str,
        *,
        source: str,
        user_text: str,
        memory_claims: tuple[OutcomeMemoryClaim, ...] = (),
    ) -> ShadowOutcomeEvent | None:
        if not turn_id.strip() or not source.strip() or not user_text.strip():
            raise OutcomeRepositoryError("turn id, source and user text are required")
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO outcome_turns
                    (id, source, user_text, response_text, memory_claims_json,
                     created_at, updated_at)
                    VALUES (?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        turn_id,
                        source.strip(),
                        user_text,
                        _memory_claims_json(memory_claims),
                        now,
                        now,
                    ),
                )
                prior = connection.execute(
                    """
                    SELECT id FROM outcome_turns
                    WHERE id != ? AND response_text IS NOT NULL
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (turn_id,),
                ).fetchone()
                if prior is None:
                    return None
                already_linked = connection.execute(
                    "SELECT 1 FROM outcome_events WHERE prior_turn_id = ?",
                    (prior["id"],),
                ).fetchone()
                if already_linked is not None:
                    return None
                event_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO outcome_events
                    (id, prior_turn_id, next_turn_id, status, assessment_json,
                     error, created_at, updated_at)
                    VALUES (?, ?, ?, 'pending', NULL, NULL, ?, ?)
                    """,
                    (event_id, prior["id"], turn_id, now, now),
                )
        except sqlite3.Error as error:
            raise OutcomeRepositoryError(
                f"could not observe outcome turn: {error}"
            ) from error
        return self.get(event_id)

    def record_response(self, turn_id: str, response_text: str) -> None:
        if not response_text.strip():
            raise OutcomeRepositoryError("assistant response is required")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE outcome_turns SET response_text = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (response_text, _now(), turn_id),
                )
                if cursor.rowcount != 1:
                    raise OutcomeRepositoryError(
                        f"outcome turn not found: {turn_id}"
                    )
        except OutcomeRepositoryError:
            raise
        except sqlite3.Error as error:
            raise OutcomeRepositoryError(
                f"could not record assistant response: {error}"
            ) from error

    def complete(
        self, event_id: str, assessment: OutcomeAssessment
    ) -> ShadowOutcomeEvent:
        event = self.get(event_id)
        if event.status != "pending":
            raise OutcomeRepositoryError(
                f"cannot complete outcome {event_id} from {event.status}"
            )
        self._update(
            event_id,
            "completed",
            json.dumps(
                assessment.to_dict(), ensure_ascii=False, separators=(",", ":")
            ),
            None,
        )
        return self.get(event_id)

    def fail(self, event_id: str, error: str) -> ShadowOutcomeEvent:
        event = self.get(event_id)
        if event.status != "pending":
            raise OutcomeRepositoryError(
                f"cannot fail outcome {event_id} from {event.status}"
            )
        self._update(
            event_id, "failed", None, error.strip() or "unknown outcome failure"
        )
        return self.get(event_id)

    def get(self, event_id: str) -> ShadowOutcomeEvent:
        try:
            with self._connect() as connection:
                row = connection.execute(_EVENT_SELECT + " WHERE e.id = ?", (event_id,)).fetchone()
        except sqlite3.Error as error:
            raise OutcomeRepositoryError(f"could not read outcome: {error}") from error
        if row is None:
            raise OutcomeRepositoryError(f"outcome not found: {event_id}")
        return _event_from_row(row)

    def list(
        self, *, status: str | None = None, limit: int | None = None
    ) -> tuple[ShadowOutcomeEvent, ...]:
        if status is not None and status not in EVENT_STATUSES:
            raise OutcomeRepositoryError(f"unsupported outcome status: {status}")
        if limit is not None and limit < 1:
            raise OutcomeRepositoryError("limit must be at least 1")
        sql = _EVENT_SELECT
        values: tuple[object, ...] = ()
        if status is not None:
            sql += " WHERE e.status = ?"
            values = (status,)
        sql += " ORDER BY e.created_at, e.id"
        if limit is not None:
            sql += " LIMIT ?"
            values += (limit,)
        try:
            with self._connect() as connection:
                rows = connection.execute(sql, values).fetchall()
        except sqlite3.Error as error:
            raise OutcomeRepositoryError(f"could not list outcomes: {error}") from error
        return tuple(_event_from_row(row) for row in rows)

    def list_reviewable(
        self, *, reviewer: str, limit: int | None = None
    ) -> tuple[ShadowOutcomeEvent, ...]:
        reviewer = reviewer.strip()
        if not reviewer:
            raise OutcomeRepositoryError("reviewer is required")
        if limit is not None and limit < 1:
            raise OutcomeRepositoryError("limit must be at least 1")
        sql = (
            _EVENT_SELECT
            + " LEFT JOIN outcome_reviews r ON r.event_id = e.id AND r.reviewer = ?"
            + " WHERE e.status = 'completed' AND r.id IS NULL"
            + " ORDER BY e.created_at, e.id"
        )
        values: tuple[object, ...] = (reviewer,)
        if limit is not None:
            sql += " LIMIT ?"
            values += (limit,)
        try:
            with self._connect() as connection:
                rows = connection.execute(sql, values).fetchall()
        except sqlite3.Error as error:
            raise OutcomeRepositoryError(
                f"could not list reviewable outcomes: {error}"
            ) from error
        return tuple(_event_from_row(row) for row in rows)

    def add_review(
        self,
        event_id: str,
        *,
        reviewer: str,
        outcome: str,
        intent_match: str,
        response_usefulness: str,
        affect_shift: str,
        memory_relations: tuple[ReviewedMemoryRelation, ...],
        improvement_confirmation: str,
    ) -> OutcomeReview:
        event = self.get(event_id)
        if event.status != "completed":
            raise OutcomeRepositoryError(
                f"cannot review outcome {event_id} from {event.status}"
            )
        relation_ids = {relation.claim_id for relation in memory_relations}
        expected_ids = {claim.id for claim in event.prior_memory_claims}
        if relation_ids != expected_ids:
            raise OutcomeRepositoryError(
                "reviewed memory relations must contain every used memory exactly once"
            )
        try:
            review = OutcomeReview(
                id=str(uuid4()),
                event_id=event_id,
                reviewer=reviewer.strip(),
                outcome=outcome,
                intent_match=intent_match,
                response_usefulness=response_usefulness,
                affect_shift=affect_shift,
                memory_relations=memory_relations,
                improvement_confirmation=improvement_confirmation,
                created_at=_now(),
            )
        except OutcomeInterpretationError as error:
            raise OutcomeRepositoryError(f"invalid outcome review: {error}") from error
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO outcome_reviews
                    (id, event_id, reviewer, outcome, intent_match,
                     response_usefulness, affect_shift, memory_relations_json,
                     improvement_confirmation, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review.id,
                        review.event_id,
                        review.reviewer,
                        review.outcome,
                        review.intent_match,
                        review.response_usefulness,
                        review.affect_shift,
                        _reviewed_memory_relations_json(review.memory_relations),
                        review.improvement_confirmation,
                        review.created_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise OutcomeRepositoryError(
                f"outcome {event_id} was already reviewed by {review.reviewer}"
            ) from error
        except sqlite3.Error as error:
            raise OutcomeRepositoryError(f"could not save outcome review: {error}") from error
        return review

    def list_reviews(self, *, reviewer: str | None = None) -> tuple[OutcomeReview, ...]:
        sql = "SELECT * FROM outcome_reviews"
        values: tuple[object, ...] = ()
        if reviewer is not None:
            reviewer = reviewer.strip()
            if not reviewer:
                raise OutcomeRepositoryError("reviewer is required")
            sql += " WHERE reviewer = ?"
            values = (reviewer,)
        sql += " ORDER BY created_at, id"
        try:
            with self._connect() as connection:
                rows = connection.execute(sql, values).fetchall()
        except sqlite3.Error as error:
            raise OutcomeRepositoryError(f"could not list outcome reviews: {error}") from error
        return tuple(_review_from_row(row) for row in rows)

    def _update(
        self,
        event_id: str,
        status: str,
        assessment_json: str | None,
        error: str | None,
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE outcome_events
                    SET status = ?, assessment_json = ?, error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, assessment_json, error, _now(), event_id),
                )
        except sqlite3.Error as sqlite_error:
            raise OutcomeRepositoryError(
                f"could not update outcome: {sqlite_error}"
            ) from sqlite_error

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS outcome_schema "
                    "(version INTEGER NOT NULL)"
                )
                version = connection.execute(
                    "SELECT version FROM outcome_schema "
                    "ORDER BY version DESC LIMIT 1"
                ).fetchone()
                if version is not None and version["version"] > self._SCHEMA_VERSION:
                    raise OutcomeRepositoryError(
                        f"unsupported schema version {version['version']}"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS outcome_turns (
                        id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        user_text TEXT NOT NULL,
                        response_text TEXT,
                        memory_claims_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS outcome_events (
                        id TEXT PRIMARY KEY,
                        prior_turn_id TEXT NOT NULL UNIQUE,
                        next_turn_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        assessment_json TEXT,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(prior_turn_id) REFERENCES outcome_turns(id),
                        FOREIGN KEY(next_turn_id) REFERENCES outcome_turns(id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS outcome_reviews (
                        id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL,
                        reviewer TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        intent_match TEXT NOT NULL,
                        response_usefulness TEXT NOT NULL,
                        affect_shift TEXT NOT NULL,
                        memory_relations_json TEXT NOT NULL,
                        improvement_confirmation TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(event_id, reviewer),
                        FOREIGN KEY(event_id) REFERENCES outcome_events(id)
                    )
                    """
                )
                if version is None:
                    connection.execute(
                        "INSERT INTO outcome_schema VALUES (?)",
                        (self._SCHEMA_VERSION,),
                    )
                else:
                    if version["version"] == 1:
                        columns = {
                            row["name"]
                            for row in connection.execute(
                                "PRAGMA table_info(outcome_turns)"
                            ).fetchall()
                        }
                        if "memory_claims_json" not in columns:
                            connection.execute(
                                "ALTER TABLE outcome_turns ADD COLUMN "
                                "memory_claims_json TEXT NOT NULL DEFAULT '[]'"
                            )
                    if version["version"] < self._SCHEMA_VERSION:
                        connection.execute(
                            "UPDATE outcome_schema SET version = ?",
                            (self._SCHEMA_VERSION,),
                        )
        except OutcomeRepositoryError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise OutcomeRepositoryError(
                f"could not initialize outcome storage: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


@dataclass(frozen=True)
class OutcomeProcessingResult:
    processed: int
    completed: int
    failed: int
    deferred: int


class ShadowOutcomeService:
    def __init__(
        self,
        outcome_repository: SqliteOutcomeRepository,
        turn_repository: SqliteTurnUnderstandingRepository,
        interpreter: OutcomeInterpreter,
    ) -> None:
        self._outcomes = outcome_repository
        self._turns = turn_repository
        self._interpreter = interpreter

    def process_pending(self, *, limit: int = 10) -> OutcomeProcessingResult:
        pending = self._outcomes.list(status="pending", limit=limit)
        completed = 0
        failed = 0
        deferred = 0
        for event in pending:
            prior = self._turns.get(event.prior_turn_id)
            if prior.status == "pending":
                deferred += 1
                continue
            if prior.status == "failed" or prior.understanding is None:
                self._outcomes.fail(
                    event.id,
                    f"prior TurnUnderstanding is {prior.status}",
                )
                failed += 1
                continue
            request = OutcomeInterpretationRequest(
                prior_user_text=event.prior_user_text,
                prior_understanding=prior.understanding,
                assistant_response=event.assistant_response,
                next_user_text=event.next_user_text,
                memory_claims=event.prior_memory_claims,
            )
            try:
                assessment = self._interpreter.evaluate(request)
            except OutcomeInterpreterUnavailableError:
                deferred += 1
            except OutcomeInterpretationError as error:
                self._outcomes.fail(event.id, str(error))
                failed += 1
            else:
                self._outcomes.complete(event.id, assessment)
                completed += 1
        return OutcomeProcessingResult(len(pending), completed, failed, deferred)


_EVENT_SELECT = """
SELECT e.*, prior.user_text AS prior_user_text,
       prior.response_text AS assistant_response,
       prior.memory_claims_json AS prior_memory_claims_json,
       next.user_text AS next_user_text
FROM outcome_events e
JOIN outcome_turns prior ON prior.id = e.prior_turn_id
JOIN outcome_turns next ON next.id = e.next_turn_id
"""


def _event_from_row(row: sqlite3.Row) -> ShadowOutcomeEvent:
    status = row["status"]
    if status not in EVENT_STATUSES:
        raise OutcomeRepositoryError(f"stored outcome has unsupported status: {status}")
    assessment = None
    if row["assessment_json"] is not None:
        try:
            payload = json.loads(row["assessment_json"])
            assessment = OutcomeAssessment.from_dict(
                payload, evidence_text=row["next_user_text"]
            )
        except (json.JSONDecodeError, OutcomeInterpretationError) as error:
            raise OutcomeRepositoryError(
                f"stored outcome assessment is invalid: {error}"
            ) from error
    response = row["assistant_response"]
    if not isinstance(response, str) or not response.strip():
        raise OutcomeRepositoryError("stored outcome has no assistant response")
    return ShadowOutcomeEvent(
        id=row["id"],
        prior_turn_id=row["prior_turn_id"],
        next_turn_id=row["next_turn_id"],
        prior_user_text=row["prior_user_text"],
        assistant_response=response,
        next_user_text=row["next_user_text"],
        prior_memory_claims=_memory_claims_from_json(
            row["prior_memory_claims_json"]
        ),
        status=status,
        assessment=assessment,
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _memory_claims_json(claims: tuple[OutcomeMemoryClaim, ...]) -> str:
    ids = [claim.id for claim in claims]
    if len(ids) != len(set(ids)):
        raise OutcomeRepositoryError("memory claim ids must be unique")
    return json.dumps(
        [asdict(claim) for claim in claims],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _memory_claims_from_json(raw: object) -> tuple[OutcomeMemoryClaim, ...]:
    if not isinstance(raw, str):
        raise OutcomeRepositoryError("stored memory claims must be JSON text")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise OutcomeRepositoryError("stored memory claims are invalid JSON") from error
    if not isinstance(decoded, list):
        raise OutcomeRepositoryError("stored memory claims must be a list")
    claims: list[OutcomeMemoryClaim] = []
    try:
        for item in decoded:
            if not isinstance(item, dict) or set(item) != {"id", "kind", "content"}:
                raise OutcomeRepositoryError("stored memory claim fields are invalid")
            claims.append(
                OutcomeMemoryClaim(
                    id=item["id"],
                    kind=item["kind"],
                    content=item["content"],
                )
            )
    except (OutcomeInterpretationError, TypeError) as error:
        raise OutcomeRepositoryError(f"stored memory claim is invalid: {error}") from error
    if len({claim.id for claim in claims}) != len(claims):
        raise OutcomeRepositoryError("stored memory claim ids must be unique")
    return tuple(claims)


def _reviewed_memory_relations_json(
    relations: tuple[ReviewedMemoryRelation, ...],
) -> str:
    return json.dumps(
        [asdict(relation) for relation in relations],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _reviewed_memory_relations_from_json(
    raw: object,
) -> tuple[ReviewedMemoryRelation, ...]:
    if not isinstance(raw, str):
        raise OutcomeRepositoryError("stored reviewed memory relations must be JSON text")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise OutcomeRepositoryError(
            "stored reviewed memory relations are invalid JSON"
        ) from error
    if not isinstance(decoded, list):
        raise OutcomeRepositoryError("stored reviewed memory relations must be a list")
    try:
        relations = tuple(
            ReviewedMemoryRelation(
                claim_id=item["claim_id"],
                relation=item["relation"],
            )
            for item in decoded
            if isinstance(item, dict) and set(item) == {"claim_id", "relation"}
        )
    except (KeyError, TypeError, OutcomeInterpretationError) as error:
        raise OutcomeRepositoryError(
            f"stored reviewed memory relation is invalid: {error}"
        ) from error
    if len(relations) != len(decoded):
        raise OutcomeRepositoryError("stored reviewed memory relation fields are invalid")
    if len({relation.claim_id for relation in relations}) != len(relations):
        raise OutcomeRepositoryError("stored reviewed memory claim ids must be unique")
    return relations


def _review_from_row(row: sqlite3.Row) -> OutcomeReview:
    try:
        return OutcomeReview(
            id=row["id"],
            event_id=row["event_id"],
            reviewer=row["reviewer"],
            outcome=row["outcome"],
            intent_match=row["intent_match"],
            response_usefulness=row["response_usefulness"],
            affect_shift=row["affect_shift"],
            memory_relations=_reviewed_memory_relations_from_json(
                row["memory_relations_json"]
            ),
            improvement_confirmation=row["improvement_confirmation"],
            created_at=row["created_at"],
        )
    except (OutcomeInterpretationError, TypeError) as error:
        raise OutcomeRepositoryError(f"stored outcome review is invalid: {error}") from error


def _signals(
    value: object, evidence_text: str, label: str
) -> tuple[OutcomeSignal, ...]:
    signals: list[OutcomeSignal] = []
    for item in _sequence(value, label):
        data = _mapping(item, label)
        if set(data) != {"content", "confidence", "evidence"}:
            raise OutcomeInterpretationError(
                f"{label} entries require content, confidence and evidence"
            )
        signals.append(
            OutcomeSignal(
                _string(data["content"], f"{label}.content"),
                _confidence(data["confidence"], f"{label}.confidence"),
                _evidence(data["evidence"], evidence_text, f"{label}.evidence"),
            )
        )
    return tuple(signals)


def _evidence(value: object, source: str, label: str) -> tuple[str, ...]:
    evidence = _strings(value, label)
    if not evidence:
        raise OutcomeInterpretationError(f"{label} must contain at least one quote")
    for quote in evidence:
        if quote not in source:
            raise OutcomeInterpretationError(
                f"{label} quote must be an exact substring of the next user turn: {quote!r}"
            )
    return evidence


def _enum(value: object, allowed: frozenset[str], label: str) -> str:
    parsed = _string(value, label)
    if parsed not in allowed:
        raise OutcomeInterpretationError(f"unsupported {label}: {parsed}")
    return parsed


def _confidence(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OutcomeInterpretationError(f"{label} confidence must be a number")
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise OutcomeInterpretationError(
            f"{label} confidence must be between 0 and 1"
        )
    return parsed


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise OutcomeInterpretationError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise OutcomeInterpretationError(f"{label} must be an array")
    return tuple(value)


def _strings(value: object, label: str) -> tuple[str, ...]:
    return tuple(_string(item, label) for item in _sequence(value, label))


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutcomeInterpretationError(f"{label} must be a non-empty string")
    return value.strip()


def _now() -> str:
    return datetime.now(UTC).isoformat()
