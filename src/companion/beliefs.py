"""Persistent, evidence-backed judgements that belong to 겨울이.

A belief is not a user fact and it is not part of the immutable persona.  It is
the revisable conclusion 겨울이 has reached about a subject.  Keeping that
distinction explicit prevents two equally bad substitutes for a point of view:
hard-coded opinions and a fresh, contradictory improvisation on every turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

_TERMS = re.compile(r"[0-9A-Za-z가-힣_+-]+")

_CREATE_BELIEFS_TABLE = """
CREATE TABLE IF NOT EXISTS beliefs (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    stance TEXT NOT NULL,
    rationale TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    supersedes TEXT
)
"""


class BeliefRepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Belief:
    id: str
    subject: str
    stance: str
    rationale: str
    confidence: float
    evidence: tuple[str, ...]
    status: str
    source: str
    created_at: str
    updated_at: str
    supersedes: str | None


class SqliteBeliefRepository:
    """Audit-friendly storage for candidate, active and revised beliefs."""

    _STATUSES = ("candidate", "active", "deprecated", "rejected")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def add_candidate(
        self,
        *,
        subject: str,
        stance: str,
        rationale: str,
        confidence: float,
        evidence: tuple[str, ...],
        source: str = "manual",
        supersedes: str | None = None,
    ) -> Belief:
        subject = subject.strip()
        stance = stance.strip()
        rationale = rationale.strip()
        source = source.strip()
        evidence = tuple(item.strip() for item in evidence if item.strip())
        if not subject or not stance or not rationale or not source:
            raise BeliefRepositoryError(
                "belief subject, stance, rationale and source are required"
            )
        if not 0.0 <= confidence <= 1.0:
            raise BeliefRepositoryError("belief confidence must be between 0 and 1")
        if not evidence:
            raise BeliefRepositoryError("belief evidence must contain at least one reference")
        if supersedes is not None:
            old = self.get(supersedes)
            if old.subject != subject:
                raise BeliefRepositoryError("a belief revision cannot change its subject")
        now = _now()
        belief = Belief(
            id=str(uuid4()),
            subject=subject,
            stance=stance,
            rationale=rationale,
            confidence=confidence,
            evidence=evidence,
            status="candidate",
            source=source,
            created_at=now,
            updated_at=now,
            supersedes=supersedes,
        )
        self._write(
            "INSERT INTO beliefs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _values(belief),
        )
        return belief

    def transition(self, belief_id: str, status: str) -> Belief:
        if status not in self._STATUSES:
            raise BeliefRepositoryError(f"unsupported belief status: {status}")
        current = self.get(belief_id)
        allowed = {
            "candidate": ("active", "rejected"),
            "active": ("deprecated",),
        }
        if status not in allowed.get(current.status, ()):
            raise BeliefRepositoryError(
                f"cannot transition belief {belief_id} from {current.status} to {status}"
            )
        try:
            with self._connect() as connection:
                if status == "active":
                    competing = connection.execute(
                        """
                        SELECT id FROM beliefs
                        WHERE subject = ? AND status = 'active' AND id != ?
                        LIMIT 1
                        """,
                        (current.subject, current.supersedes or ""),
                    ).fetchone()
                    if competing is not None:
                        raise BeliefRepositoryError(
                            "active belief already exists for subject "
                            f"{current.subject!r}: revise {competing['id']} instead"
                        )
                now = _now()
                connection.execute(
                    "UPDATE beliefs SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, belief_id),
                )
                if status == "active" and current.supersedes:
                    connection.execute(
                        """
                        UPDATE beliefs SET status = 'deprecated', updated_at = ?
                        WHERE id = ? AND status = 'active'
                        """,
                        (now, current.supersedes),
                    )
        except sqlite3.Error as error:
            raise BeliefRepositoryError(f"could not update belief: {error}") from error
        return self.get(belief_id)

    def revise(
        self,
        belief_id: str,
        *,
        stance: str,
        rationale: str,
        confidence: float,
        evidence: tuple[str, ...],
        source: str = "manual",
    ) -> Belief:
        old = self.get(belief_id)
        if old.status != "active":
            raise BeliefRepositoryError("only an active belief can be revised")
        return self.add_candidate(
            subject=old.subject,
            stance=stance,
            rationale=rationale,
            confidence=confidence,
            evidence=evidence,
            source=source,
            supersedes=old.id,
        )

    def get(self, belief_id: str) -> Belief:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM beliefs WHERE id = ?", (belief_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise BeliefRepositoryError(f"could not read belief: {error}") from error
        if row is None:
            raise BeliefRepositoryError(f"belief not found: {belief_id}")
        return _from_row(row)

    def list(self) -> tuple[Belief, ...]:
        try:
            with self._connect() as connection:
                return tuple(
                    _from_row(row)
                    for row in connection.execute(
                        "SELECT * FROM beliefs ORDER BY created_at, id"
                    )
                )
        except sqlite3.Error as error:
            raise BeliefRepositoryError(f"could not list beliefs: {error}") from error

    def list_active(self) -> tuple[Belief, ...]:
        return tuple(belief for belief in self.list() if belief.status == "active")

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(_CREATE_BELIEFS_TABLE)
        except (OSError, sqlite3.Error) as error:
            raise BeliefRepositoryError(f"could not initialize belief storage: {error}") from error

    def _write(self, sql: str, values: tuple[object, ...]) -> None:
        try:
            with self._connect() as connection:
                connection.execute(sql, values)
        except sqlite3.Error as error:
            raise BeliefRepositoryError(f"could not write belief: {error}") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


class ActiveBeliefRetriever:
    def __init__(
        self,
        repository: SqliteBeliefRepository,
        *,
        max_beliefs: int = 3,
        max_characters: int = 1400,
    ) -> None:
        self._repository = repository
        self._max_beliefs = max_beliefs
        self._max_characters = max_characters

    def retrieve(self, query: str) -> tuple[Belief, ...]:
        query_terms = _terms(query)
        ranked: list[tuple[int, float, str, Belief]] = []
        for belief in self._repository.list_active():
            overlap = len(
                query_terms & _terms(f"{belief.subject} {belief.stance} {belief.rationale}")
            )
            if overlap:
                ranked.append((overlap, belief.confidence, belief.updated_at, belief))
        selected: list[Belief] = []
        length = 0
        for _, _, _, belief in sorted(
            ranked,
            key=lambda item: (-item[0], -item[1], item[2], item[3].id),
        ):
            belief_length = len(belief.subject) + len(belief.stance) + len(belief.rationale)
            if len(selected) == self._max_beliefs or length + belief_length > self._max_characters:
                continue
            selected.append(belief)
            length += belief_length
        return tuple(selected)


def belief_context(beliefs: tuple[Belief, ...]) -> str:
    lines = [
        "다음은 겨울이가 근거를 검토한 뒤 유지 중인 관점이야.",
        "고정된 사실이 아니라 수정 가능한 판단이므로, 새 근거와 충돌하면 확신하지 마.",
    ]
    for belief in beliefs:
        evidence = ", ".join(belief.evidence)
        lines.append(
            f"- [{belief.id}] 주제={belief.subject}; 입장={belief.stance}; "
            f"이유={belief.rationale}; 신뢰도={belief.confidence:.2f}; 근거={evidence}"
        )
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _values(belief: Belief) -> tuple[object, ...]:
    return (
        belief.id,
        belief.subject,
        belief.stance,
        belief.rationale,
        belief.confidence,
        json.dumps(belief.evidence, ensure_ascii=False),
        belief.status,
        belief.source,
        belief.created_at,
        belief.updated_at,
        belief.supersedes,
    )


def _from_row(row: sqlite3.Row) -> Belief:
    raw = dict(row)
    try:
        evidence = json.loads(raw.pop("evidence"))
    except (TypeError, json.JSONDecodeError) as error:
        raise BeliefRepositoryError("belief evidence is not valid JSON") from error
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise BeliefRepositoryError("belief evidence must be a JSON string list")
    return Belief(evidence=tuple(evidence), **raw)


def _terms(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TERMS.finditer(text)}

