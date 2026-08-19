"""Persistent conversational improvement state owned by Winter and Cheonu."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Protocol, cast
from uuid import uuid4

IMPROVEMENT_STATUSES = frozenset(
    {"observed", "discussing", "proposed", "approved", "rejected"}
)
_TRANSITIONS: dict[str, frozenset[str]] = {
    "observed": frozenset({"discussing", "rejected"}),
    "discussing": frozenset({"proposed", "rejected"}),
    "proposed": frozenset({"approved", "discussing", "rejected"}),
    "approved": frozenset(),
    "rejected": frozenset(),
}
_EVIDENCE_REFERENCE = re.compile(
    r"^(?:conversation|winter_event|evaluation|outcome|latency|issue|test):\S+$"
)


class ImprovementLedgerError(RuntimeError):
    """Raised when an improvement loses evidence or skips an approval boundary."""


@dataclass(frozen=True)
class ImprovementProposal:
    improvement_id: str
    version: int
    proposal_hash: str
    why_it_matters: str
    solution: str
    alternatives: tuple[str, ...]
    expected_effect: str
    risks: tuple[str, ...]
    privacy_impact: str
    implementation_scope: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    rollback_plan: str
    created_at: str


@dataclass(frozen=True)
class ImprovementDecision:
    id: str
    improvement_id: str
    proposal_version: int
    proposal_hash: str
    kind: str
    decided_by: str
    evidence: str
    created_at: str


@dataclass(frozen=True)
class ImprovementTransition:
    id: str
    improvement_id: str
    from_status: str
    to_status: str
    reason: str | None
    created_at: str


@dataclass(frozen=True)
class ImprovementItem:
    id: str
    title: str
    problem: str
    status: str
    evidence: tuple[str, ...]
    current_proposal_version: int | None
    proposals: tuple[ImprovementProposal, ...]
    decisions: tuple[ImprovementDecision, ...]
    transitions: tuple[ImprovementTransition, ...]
    created_at: str
    updated_at: str

    @property
    def current_proposal(self) -> ImprovementProposal | None:
        if self.current_proposal_version is None:
            return None
        return next(
            (
                proposal
                for proposal in self.proposals
                if proposal.version == self.current_proposal_version
            ),
            None,
        )


@dataclass(frozen=True)
class ImprovementSummary:
    id: str
    title: str
    problem: str
    status: str
    proposal_version: int | None
    implementation_approved: bool


@dataclass(frozen=True)
class ImprovementSnapshot:
    items: tuple[ImprovementSummary, ...]

    def system_message(self) -> str:
        lines = [
            "[지속 중인 개선 안건 — 읽기 전용]",
            "이 목록은 토론 상태이며 자동 실행 명령이 아니다.",
        ]
        if not self.items:
            lines.append("- 현재 지속 중인 개선 안건 없음")
        for item in self.items:
            version = (
                f"proposal v{item.proposal_version}"
                if item.proposal_version is not None
                else "proposal 없음"
            )
            lines.append(
                f"- {item.title} [{item.status}, {version}]: {item.problem}"
            )
            if item.implementation_approved:
                lines.append(
                    "  구현만 승인됨. 아직 구현·검증·채택됐다고 말하거나 Worker를 실행하지 마."
                )
        lines.append("채택 승인은 구현 승인과 별개이며 이 snapshot은 상태를 변경하지 않아.")
        return "\n".join(lines)


class ImprovementSnapshotProvider(Protocol):
    def snapshot(self) -> ImprovementSnapshot: ...


class SqliteImprovementLedger:
    """Store immutable proposals and explicit implementation approvals."""

    _SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def observe(
        self,
        *,
        title: str,
        problem: str,
        evidence: tuple[str, ...],
    ) -> ImprovementItem:
        title = _required(title, "improvement title")
        problem = _required(problem, "improvement problem")
        evidence = _evidence_items(evidence)
        improvement_id = str(uuid4())
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO improvement_items
                    (id, title, problem, status, current_proposal_version,
                     created_at, updated_at)
                    VALUES (?, ?, ?, 'observed', NULL, ?, ?)
                    """,
                    (improvement_id, title, problem, now, now),
                )
                connection.executemany(
                    "INSERT INTO improvement_evidence VALUES (?, ?, ?, ?, ?)",
                    (
                        (str(uuid4()), improvement_id, position, reference, now)
                        for position, reference in enumerate(evidence)
                    ),
                )
        except (OSError, sqlite3.Error) as error:
            raise ImprovementLedgerError(
                f"could not observe improvement: {error}"
            ) from error
        return self.get(improvement_id)

    def start_discussion(self, improvement_id: str) -> ImprovementItem:
        return self._transition(improvement_id, "discussing")

    def propose(
        self,
        improvement_id: str,
        *,
        why_it_matters: str,
        solution: str,
        alternatives: tuple[str, ...],
        expected_effect: str,
        risks: tuple[str, ...],
        privacy_impact: str,
        implementation_scope: tuple[str, ...],
        acceptance_criteria: tuple[str, ...],
        rollback_plan: str,
    ) -> ImprovementItem:
        payload: dict[str, object] = {
            "why_it_matters": _required(why_it_matters, "why it matters"),
            "solution": _required(solution, "solution"),
            "alternatives": _items(alternatives, "alternatives"),
            "expected_effect": _required(expected_effect, "expected effect"),
            "risks": _items(risks, "risks"),
            "privacy_impact": _required(privacy_impact, "privacy impact"),
            "implementation_scope": _items(
                implementation_scope, "implementation scope"
            ),
            "acceptance_criteria": _items(
                acceptance_criteria, "acceptance criteria"
            ),
            "rollback_plan": _required(rollback_plan, "rollback plan"),
        }
        now = _now()
        try:
            with self._connect() as connection:
                row = self._item_row(connection, improvement_id)
                if row["status"] != "discussing":
                    raise ImprovementLedgerError(
                        "only a discussing improvement can receive a proposal"
                    )
                if not self._evidence_rows(connection, improvement_id):
                    raise ImprovementLedgerError(
                        "an improvement proposal requires exact evidence references"
                    )
                latest = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version "
                    "FROM improvement_proposals WHERE improvement_id = ?",
                    (improvement_id,),
                ).fetchone()
                version = int(latest["version"]) + 1
                proposal_hash = _proposal_hash(improvement_id, version, payload)
                connection.execute(
                    """
                    INSERT INTO improvement_proposals
                    (improvement_id, version, proposal_hash, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        improvement_id,
                        version,
                        proposal_hash,
                        _json(payload),
                        now,
                    ),
                )
                self._transition_in(
                    connection,
                    row,
                    "proposed",
                    reason=f"proposal v{version}:{proposal_hash}",
                    current_proposal_version=version,
                    now=now,
                )
        except ImprovementLedgerError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise ImprovementLedgerError(
                f"could not propose improvement: {error}"
            ) from error
        return self.get(improvement_id)

    def approve_implementation(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
        decided_by: str,
        evidence: str,
    ) -> ImprovementItem:
        decided_by = _required(decided_by, "implementation approver")
        evidence = _required(evidence, "implementation approval evidence")
        if not _EVIDENCE_REFERENCE.fullmatch(evidence):
            raise ImprovementLedgerError(
                "implementation approval evidence must be a stable reference"
            )
        proposal_hash = _required(proposal_hash, "proposal hash")
        now = _now()
        try:
            with self._connect() as connection:
                row = self._item_row(connection, improvement_id)
                if row["status"] != "proposed":
                    raise ImprovementLedgerError(
                        "implementation approval requires a proposed improvement"
                    )
                proposal = self._proposal_row(
                    connection, improvement_id, proposal_version
                )
                if row["current_proposal_version"] != proposal_version:
                    raise ImprovementLedgerError(
                        "implementation approval must target the current proposal version"
                    )
                if proposal["proposal_hash"] != proposal_hash:
                    raise ImprovementLedgerError(
                        "implementation approval proposal hash does not match"
                    )
                connection.execute(
                    """
                    INSERT INTO improvement_decisions
                    (id, improvement_id, proposal_version, proposal_hash, kind,
                     decided_by, evidence, created_at)
                    VALUES (?, ?, ?, ?, 'implementation_approval', ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        improvement_id,
                        proposal_version,
                        proposal_hash,
                        decided_by,
                        evidence,
                        now,
                    ),
                )
                self._transition_in(
                    connection,
                    row,
                    "approved",
                    reason="explicit implementation approval",
                    current_proposal_version=proposal_version,
                    now=now,
                )
        except sqlite3.IntegrityError as error:
            raise ImprovementLedgerError(
                "implementation approval was already recorded"
            ) from error
        except ImprovementLedgerError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise ImprovementLedgerError(
                f"could not approve improvement implementation: {error}"
            ) from error
        return self.get(improvement_id)

    def request_revision(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
        reason: str,
    ) -> ImprovementItem:
        reason = _required(reason, "revision reason")
        now = _now()
        try:
            with self._connect() as connection:
                row = self._item_row(connection, improvement_id)
                if row["status"] != "proposed":
                    raise ImprovementLedgerError(
                        "revision requires the current proposed improvement"
                    )
                proposal = self._proposal_row(
                    connection, improvement_id, proposal_version
                )
                if (
                    row["current_proposal_version"] != proposal_version
                    or proposal["proposal_hash"] != proposal_hash
                ):
                    raise ImprovementLedgerError(
                        "revision must target the current proposal"
                    )
                self._transition_in(
                    connection,
                    row,
                    "discussing",
                    reason=reason,
                    current_proposal_version=proposal_version,
                    now=now,
                )
        except ImprovementLedgerError:
            raise
        except sqlite3.Error as error:
            raise ImprovementLedgerError(
                f"could not request improvement revision: {error}"
            ) from error
        return self.get(improvement_id)

    def reject(self, improvement_id: str, *, reason: str) -> ImprovementItem:
        return self._transition(
            improvement_id,
            "rejected",
            reason=_required(reason, "rejection reason"),
        )

    def get(self, improvement_id: str) -> ImprovementItem:
        try:
            with self._connect() as connection:
                row = self._item_row(connection, improvement_id)
                evidence_rows = self._evidence_rows(connection, improvement_id)
                proposal_rows = connection.execute(
                    "SELECT * FROM improvement_proposals WHERE improvement_id = ? "
                    "ORDER BY version",
                    (improvement_id,),
                ).fetchall()
                decision_rows = connection.execute(
                    "SELECT * FROM improvement_decisions WHERE improvement_id = ? "
                    "ORDER BY created_at, id",
                    (improvement_id,),
                ).fetchall()
                transition_rows = connection.execute(
                    "SELECT * FROM improvement_transitions WHERE improvement_id = ? "
                    "ORDER BY created_at, id",
                    (improvement_id,),
                ).fetchall()
        except ImprovementLedgerError:
            raise
        except sqlite3.Error as error:
            raise ImprovementLedgerError(
                f"could not read improvement: {error}"
            ) from error
        return _item(
            row,
            evidence_rows,
            proposal_rows,
            decision_rows,
            transition_rows,
        )

    def list(self, *, active_only: bool = False) -> tuple[ImprovementItem, ...]:
        sql = "SELECT id FROM improvement_items"
        if active_only:
            sql += " WHERE status != 'rejected'"
        sql += " ORDER BY created_at, id"
        try:
            with self._connect() as connection:
                ids = [row["id"] for row in connection.execute(sql).fetchall()]
        except sqlite3.Error as error:
            raise ImprovementLedgerError(
                f"could not list improvements: {error}"
            ) from error
        return tuple(self.get(improvement_id) for improvement_id in ids)

    def _transition(
        self,
        improvement_id: str,
        status: str,
        *,
        reason: str | None = None,
    ) -> ImprovementItem:
        now = _now()
        try:
            with self._connect() as connection:
                row = self._item_row(connection, improvement_id)
                self._transition_in(
                    connection,
                    row,
                    status,
                    reason=reason,
                    current_proposal_version=row["current_proposal_version"],
                    now=now,
                )
        except ImprovementLedgerError:
            raise
        except sqlite3.Error as error:
            raise ImprovementLedgerError(
                f"could not transition improvement: {error}"
            ) from error
        return self.get(improvement_id)

    @staticmethod
    def _transition_in(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        status: str,
        *,
        reason: str | None,
        current_proposal_version: int | None,
        now: str,
    ) -> None:
        current_status = str(row["status"])
        if status not in IMPROVEMENT_STATUSES or status not in _TRANSITIONS[current_status]:
            raise ImprovementLedgerError(
                f"cannot transition improvement from {current_status} to {status}"
            )
        updated = connection.execute(
            """
            UPDATE improvement_items
            SET status = ?, current_proposal_version = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                status,
                current_proposal_version,
                now,
                row["id"],
                current_status,
            ),
        )
        if updated.rowcount != 1:
            raise ImprovementLedgerError(
                "improvement changed concurrently; retry from the latest state"
            )
        connection.execute(
            "INSERT INTO improvement_transitions VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid4()), row["id"], current_status, status, reason, now),
        )

    @staticmethod
    def _item_row(
        connection: sqlite3.Connection, improvement_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM improvement_items WHERE id = ?", (improvement_id,)
        ).fetchone()
        if row is None:
            raise ImprovementLedgerError(f"improvement not found: {improvement_id}")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _proposal_row(
        connection: sqlite3.Connection,
        improvement_id: str,
        version: int,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM improvement_proposals "
            "WHERE improvement_id = ? AND version = ?",
            (improvement_id, version),
        ).fetchone()
        if row is None:
            raise ImprovementLedgerError("improvement proposal version not found")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _evidence_rows(
        connection: sqlite3.Connection, improvement_id: str
    ) -> builtins.list[sqlite3.Row]:
        rows = connection.execute(
            "SELECT * FROM improvement_evidence WHERE improvement_id = ? "
            "ORDER BY position, id",
            (improvement_id,),
        ).fetchall()
        return cast(builtins.list[sqlite3.Row], rows)

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS improvement_schema (
                        version INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS improvement_items (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        problem TEXT NOT NULL,
                        status TEXT NOT NULL,
                        current_proposal_version INTEGER,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS improvement_evidence (
                        id TEXT PRIMARY KEY,
                        improvement_id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        reference TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(improvement_id) REFERENCES improvement_items(id)
                    );
                    CREATE TABLE IF NOT EXISTS improvement_proposals (
                        improvement_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        proposal_hash TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(improvement_id, version),
                        UNIQUE(improvement_id, proposal_hash),
                        FOREIGN KEY(improvement_id) REFERENCES improvement_items(id)
                    );
                    CREATE TABLE IF NOT EXISTS improvement_decisions (
                        id TEXT PRIMARY KEY,
                        improvement_id TEXT NOT NULL,
                        proposal_version INTEGER NOT NULL,
                        proposal_hash TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        decided_by TEXT NOT NULL,
                        evidence TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(improvement_id, proposal_version, kind),
                        FOREIGN KEY(improvement_id) REFERENCES improvement_items(id)
                    );
                    CREATE TABLE IF NOT EXISTS improvement_transitions (
                        id TEXT PRIMARY KEY,
                        improvement_id TEXT NOT NULL,
                        from_status TEXT NOT NULL,
                        to_status TEXT NOT NULL,
                        reason TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(improvement_id) REFERENCES improvement_items(id)
                    );
                    """
                )
                version = connection.execute(
                    "SELECT version FROM improvement_schema LIMIT 1"
                ).fetchone()
                if version is None:
                    connection.execute(
                        "INSERT INTO improvement_schema VALUES (?)",
                        (self._SCHEMA_VERSION,),
                    )
                elif version["version"] != self._SCHEMA_VERSION:
                    raise ImprovementLedgerError(
                        f"unsupported improvement schema version {version['version']}"
                    )
        except ImprovementLedgerError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise ImprovementLedgerError(
                f"could not initialize improvement ledger: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class PersistentImprovementSnapshot:
    def __init__(self, ledger: SqliteImprovementLedger) -> None:
        self._ledger = ledger

    def snapshot(self) -> ImprovementSnapshot:
        return ImprovementSnapshot(
            tuple(
                ImprovementSummary(
                    id=item.id,
                    title=item.title,
                    problem=item.problem,
                    status=item.status,
                    proposal_version=item.current_proposal_version,
                    implementation_approved=any(
                        decision.kind == "implementation_approval"
                        for decision in item.decisions
                    ),
                )
                for item in self._ledger.list(active_only=True)
            )
        )


def _item(
    row: sqlite3.Row,
    evidence_rows: list[sqlite3.Row],
    proposal_rows: list[sqlite3.Row],
    decision_rows: list[sqlite3.Row],
    transition_rows: list[sqlite3.Row],
) -> ImprovementItem:
    return ImprovementItem(
        id=row["id"],
        title=row["title"],
        problem=row["problem"],
        status=row["status"],
        evidence=tuple(item["reference"] for item in evidence_rows),
        current_proposal_version=row["current_proposal_version"],
        proposals=tuple(_proposal(item) for item in proposal_rows),
        decisions=tuple(
            ImprovementDecision(
                id=item["id"],
                improvement_id=item["improvement_id"],
                proposal_version=item["proposal_version"],
                proposal_hash=item["proposal_hash"],
                kind=item["kind"],
                decided_by=item["decided_by"],
                evidence=item["evidence"],
                created_at=item["created_at"],
            )
            for item in decision_rows
        ),
        transitions=tuple(
            ImprovementTransition(
                id=item["id"],
                improvement_id=item["improvement_id"],
                from_status=item["from_status"],
                to_status=item["to_status"],
                reason=item["reason"],
                created_at=item["created_at"],
            )
            for item in transition_rows
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _proposal(row: sqlite3.Row) -> ImprovementProposal:
    payload = json.loads(row["payload_json"])
    return ImprovementProposal(
        improvement_id=row["improvement_id"],
        version=row["version"],
        proposal_hash=row["proposal_hash"],
        why_it_matters=payload["why_it_matters"],
        solution=payload["solution"],
        alternatives=tuple(payload["alternatives"]),
        expected_effect=payload["expected_effect"],
        risks=tuple(payload["risks"]),
        privacy_impact=payload["privacy_impact"],
        implementation_scope=tuple(payload["implementation_scope"]),
        acceptance_criteria=tuple(payload["acceptance_criteria"]),
        rollback_plan=payload["rollback_plan"],
        created_at=row["created_at"],
    )


def _proposal_hash(
    improvement_id: str,
    version: int,
    payload: dict[str, object],
) -> str:
    canonical = {
        "improvement_id": improvement_id,
        "version": version,
        **payload,
    }
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ImprovementLedgerError(f"{field} is required")
    return normalized


def _items(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
    if not normalized:
        raise ImprovementLedgerError(f"{field} requires at least one item")
    return normalized


def _evidence_items(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = _items(values, "improvement evidence")
    if any(not _EVIDENCE_REFERENCE.fullmatch(item) for item in normalized):
        raise ImprovementLedgerError(
            "improvement evidence must contain only stable references"
        )
    return normalized


def _now() -> str:
    return datetime.now(UTC).isoformat()
