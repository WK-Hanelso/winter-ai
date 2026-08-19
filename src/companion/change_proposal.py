"""Persistent, evidence-derived explanations for isolated implementation results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol

from companion.isolated_work import (
    ChangedFile,
    ImplementationAuthorization,
    IsolatedWorkError,
    IsolatedWorkResult,
    VerificationResult,
)

_PROPOSAL_STATUSES = frozenset(
    {
        "awaiting_adoption",
        "verification_failed",
        "adoption_approved",
        "applying",
        "applied",
        "apply_failed",
        "stale",
        "rolling_back",
        "rolled_back",
        "rollback_failed",
        "revision_requested",
        "rejected",
    }
)
_DECISIONS = {
    "adopt": "adoption_approved",
    "revise": "revision_requested",
    "reject": "rejected",
}


class ChangeProposalError(RuntimeError):
    """Raised when a stored proposal or adoption decision is inconsistent."""


class IsolatedWorkExecutor(Protocol):
    def execute(
        self, authorization: ImplementationAuthorization
    ) -> IsolatedWorkResult: ...


@dataclass(frozen=True)
class ChangeExplanation:
    proposal_id: str
    headline: str
    purpose: str
    changes: tuple[str, ...]
    verification: tuple[str, ...]
    risks: tuple[str, ...]
    worker_notes: tuple[str, ...]
    recommendation: str
    adoption_question: str | None
    message: str

    def __post_init__(self) -> None:
        required = (
            self.proposal_id,
            self.headline,
            self.purpose,
            self.recommendation,
            self.message,
        )
        if not all(value.strip() for value in required):
            raise ChangeProposalError("수정 설명의 필수 항목이 비어 있습니다.")
        if not self.changes or not self.verification:
            raise ChangeProposalError("수정 설명에는 변경과 검증 근거가 필요합니다.")
        if self.recommendation not in {"adoptable", "revise"}:
            raise ChangeProposalError("알 수 없는 수정 설명 권고입니다.")
        if self.recommendation == "adoptable" and self.adoption_question is None:
            raise ChangeProposalError("채택 가능한 수정안에는 적용 질문이 필요합니다.")
        if self.recommendation == "revise" and self.adoption_question is not None:
            raise ChangeProposalError("검증 실패 수정안에는 적용 질문을 만들 수 없습니다.")


@dataclass(frozen=True)
class ChangeProposalEvent:
    proposal_id: str
    status: str
    reason: str | None
    created_at: str


@dataclass(frozen=True)
class ChangeFileSnapshot:
    proposal_id: str
    path: str
    before_content: bytes | None
    before_sha256: str | None
    after_sha256: str


@dataclass(frozen=True)
class StoredChangeProposal:
    proposal_id: str
    authorization_id: str
    objective: str
    patch_sha256: str
    result: IsolatedWorkResult
    explanation: ChangeExplanation
    status: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if self.status not in _PROPOSAL_STATUSES:
            raise ChangeProposalError("저장된 수정안 상태가 올바르지 않습니다.")
        if self.proposal_id != self.result.proposal_id:
            raise ChangeProposalError("수정안 ID와 실행 결과 ID가 다릅니다.")
        if self.patch_sha256 != _patch_sha256(self.result.patch):
            raise ChangeProposalError("저장된 patch hash가 실제 patch와 다릅니다.")


def build_change_explanation(
    authorization: ImplementationAuthorization,
    result: IsolatedWorkResult,
) -> ChangeExplanation:
    """Explain only facts derived from approval, diff and host verification."""

    if authorization.authorization_id != result.authorization_id:
        raise ChangeProposalError("구현 승인과 Worker 결과가 같은 작업이 아닙니다.")
    changes = _describe_changes(result)
    verification = tuple(
        f"{item.name}: {_verification_label(item.status)}"
        for item in result.verifications
    )
    verification_names = ", ".join(item.name for item in result.verifications)
    risks = [f"현재 확인은 {verification_names} 범위까지야."]
    worker_notes = tuple(dict.fromkeys(result.limitations))
    failed = tuple(item for item in result.verifications if item.status != "pass")
    for item in failed:
        risk = f"{item.name} 검증이 통과하지 않았다."
        if risk not in risks:
            risks.append(risk)

    adoptable = result.status == "awaiting_adoption" and not failed
    if adoptable:
        headline = "수정안을 만들었고 현재 검증 범위는 통과했어."
        recommendation = "adoptable"
        adoption_question = "이 수정안을 실제 코드에 적용할까?"
        judgment = (
            "내 판단으로는 적용 후보로 볼 수 있어. "
            "다만 아직 실제 코드에는 적용하지 않았어."
        )
    else:
        headline = "수정안은 만들었지만 검증을 통과하지 못했어."
        recommendation = "revise"
        adoption_question = None
        judgment = "지금 상태로는 실제 코드에 적용하면 안 돼. 다시 수정하거나 검증해야 해."

    parts = [
        headline,
        f"목적은 {authorization.objective.strip()}",
        "실제로 " + " ".join(changes),
        "내가 별도로 확인한 결과는 " + ", ".join(verification) + ".",
    ]
    if risks:
        parts.append("내가 확인한 범위의 한계는 " + " ".join(risks))
    if worker_notes:
        parts.append("Worker가 덧붙인 참고사항은 " + " ".join(worker_notes))
    parts.extend(
        (
            judgment,
            f"수정안 ID는 {result.proposal_id}야.",
        )
    )
    if adoption_question is not None:
        parts.append(adoption_question)
    return ChangeExplanation(
        proposal_id=result.proposal_id,
        headline=headline,
        purpose=authorization.objective.strip(),
        changes=changes,
        verification=verification,
        risks=tuple(risks),
        worker_notes=worker_notes,
        recommendation=recommendation,
        adoption_question=adoption_question,
        message=" ".join(parts),
    )


class SqliteChangeProposalRepository:
    """Keep the exact patch, explanation and adoption decision across restarts."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def record(
        self,
        authorization: ImplementationAuthorization,
        result: IsolatedWorkResult,
    ) -> StoredChangeProposal:
        if authorization.authorization_id != result.authorization_id:
            raise ChangeProposalError("구현 승인과 Worker 결과가 일치하지 않습니다.")
        explanation = build_change_explanation(authorization, result)
        now = datetime.now(UTC).isoformat()
        proposal = StoredChangeProposal(
            proposal_id=result.proposal_id,
            authorization_id=result.authorization_id,
            objective=authorization.objective.strip(),
            patch_sha256=_patch_sha256(result.patch),
            result=result,
            explanation=explanation,
            status=result.status,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO change_proposals (
                        proposal_id, authorization_id, objective, patch_sha256,
                        result_json, explanation_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _proposal_values(proposal),
                )
                row = connection.execute(
                    "SELECT * FROM change_proposals WHERE proposal_id = ?",
                    (proposal.proposal_id,),
                ).fetchone()
                if row is None:
                    raise ChangeProposalError("수정안을 저장하지 못했습니다.")
                stored = _proposal_from_row(row)
                if _proposal_identity(stored) != _proposal_identity(proposal):
                    raise ChangeProposalError(
                        "같은 proposal ID가 다른 내용에 재사용되었습니다."
                    )
                existing_event = connection.execute(
                    "SELECT 1 FROM change_proposal_events "
                    "WHERE proposal_id = ? AND status = ? LIMIT 1",
                    (stored.proposal_id, stored.status),
                ).fetchone()
                if existing_event is None:
                    connection.execute(
                        "INSERT INTO change_proposal_events "
                        "(proposal_id, status, reason, created_at) VALUES (?, ?, ?, ?)",
                        (stored.proposal_id, stored.status, None, stored.created_at),
                    )
        except ChangeProposalError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise ChangeProposalError(f"수정안 저장에 실패했습니다: {error}") from error
        return stored

    def get(self, proposal_id: str) -> StoredChangeProposal | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM change_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise ChangeProposalError(f"수정안 조회에 실패했습니다: {error}") from error
        return _proposal_from_row(row) if row is not None else None

    def latest_awaiting_adoption(self) -> StoredChangeProposal | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM change_proposals WHERE status = 'awaiting_adoption' "
                    "ORDER BY created_at DESC, proposal_id DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error as error:
            raise ChangeProposalError(f"대기 수정안 조회에 실패했습니다: {error}") from error
        return _proposal_from_row(row) if row is not None else None

    def record_decision(
        self,
        proposal_id: str,
        *,
        decision: str,
        expected_patch_sha256: str | None = None,
        reason: str | None = None,
    ) -> StoredChangeProposal:
        if decision not in _DECISIONS:
            raise ChangeProposalError("알 수 없는 수정안 결정입니다.")
        normalized_reason = reason.strip() if reason is not None else None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM change_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
                if row is None:
                    raise ChangeProposalError("결정할 수정안을 찾지 못했습니다.")
                current = _proposal_from_row(row)
                if current.status != "awaiting_adoption":
                    raise ChangeProposalError(
                        "현재 상태의 수정안은 채택 승인이나 재작업 결정을 받을 수 없습니다."
                    )
                if decision == "adopt":
                    if expected_patch_sha256 != current.patch_sha256:
                        raise ChangeProposalError(
                            "천우가 확인한 patch hash와 현재 수정안 hash가 다릅니다."
                        )
                elif decision == "revise" and not normalized_reason:
                    raise ChangeProposalError("재작업 요청에는 이유가 필요합니다.")
                next_status = _DECISIONS[decision]
                now = datetime.now(UTC).isoformat()
                updated = connection.execute(
                    "UPDATE change_proposals SET status = ?, updated_at = ? "
                    "WHERE proposal_id = ? AND status = 'awaiting_adoption'",
                    (next_status, now, proposal_id),
                )
                if updated.rowcount != 1:
                    raise ChangeProposalError("수정안 상태가 동시에 변경되었습니다.")
                connection.execute(
                    "INSERT INTO change_proposal_events "
                    "(proposal_id, status, reason, created_at) VALUES (?, ?, ?, ?)",
                    (proposal_id, next_status, normalized_reason, now),
                )
                updated_row = connection.execute(
                    "SELECT * FROM change_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
        except ChangeProposalError:
            raise
        except sqlite3.Error as error:
            raise ChangeProposalError(f"수정안 결정 저장에 실패했습니다: {error}") from error
        if updated_row is None:
            raise ChangeProposalError("수정안 결정 결과를 다시 읽지 못했습니다.")
        return _proposal_from_row(updated_row)

    def list_events(self, proposal_id: str) -> tuple[ChangeProposalEvent, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT proposal_id, status, reason, created_at "
                    "FROM change_proposal_events WHERE proposal_id = ? "
                    "ORDER BY id",
                    (proposal_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise ChangeProposalError(f"수정안 이력 조회에 실패했습니다: {error}") from error
        return tuple(ChangeProposalEvent(**dict(row)) for row in rows)

    def mark_stale(self, proposal_id: str, *, reason: str) -> StoredChangeProposal:
        return self._transition(
            proposal_id,
            expected_status="adoption_approved",
            next_status="stale",
            reason=reason,
        )

    def start_application(
        self,
        proposal_id: str,
        snapshots: tuple[ChangeFileSnapshot, ...],
    ) -> StoredChangeProposal:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM change_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
                if row is None:
                    raise ChangeProposalError("적용할 수정안을 찾지 못했습니다.")
                current = _proposal_from_row(row)
                if current.status != "adoption_approved":
                    raise ChangeProposalError("채택 승인된 수정안만 적용할 수 있습니다.")
                _validate_apply_snapshots(current, snapshots)
                existing = connection.execute(
                    "SELECT 1 FROM change_file_snapshots WHERE proposal_id = ? LIMIT 1",
                    (proposal_id,),
                ).fetchone()
                if existing is not None:
                    raise ChangeProposalError("수정안의 적용 전 snapshot이 이미 존재합니다.")
                connection.executemany(
                    """
                    INSERT INTO change_file_snapshots (
                        proposal_id, path, before_content, before_sha256, after_sha256
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            item.proposal_id,
                            item.path,
                            item.before_content,
                            item.before_sha256,
                            item.after_sha256,
                        )
                        for item in snapshots
                    ),
                )
                now = datetime.now(UTC).isoformat()
                updated = connection.execute(
                    "UPDATE change_proposals SET status = 'applying', updated_at = ? "
                    "WHERE proposal_id = ? AND status = 'adoption_approved'",
                    (now, proposal_id),
                )
                if updated.rowcount != 1:
                    raise ChangeProposalError("수정안 상태가 동시에 변경되었습니다.")
                connection.execute(
                    "INSERT INTO change_proposal_events "
                    "(proposal_id, status, reason, created_at) VALUES (?, ?, ?, ?)",
                    (proposal_id, "applying", None, now),
                )
                updated_row = connection.execute(
                    "SELECT * FROM change_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
        except ChangeProposalError:
            raise
        except sqlite3.Error as error:
            raise ChangeProposalError(f"적용 snapshot 저장에 실패했습니다: {error}") from error
        if updated_row is None:
            raise ChangeProposalError("적용 시작 상태를 다시 읽지 못했습니다.")
        return _proposal_from_row(updated_row)

    def list_apply_snapshots(
        self, proposal_id: str
    ) -> tuple[ChangeFileSnapshot, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT proposal_id, path, before_content, before_sha256, after_sha256 "
                    "FROM change_file_snapshots WHERE proposal_id = ? ORDER BY path",
                    (proposal_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise ChangeProposalError(f"적용 snapshot 조회에 실패했습니다: {error}") from error
        return tuple(
            ChangeFileSnapshot(
                proposal_id=str(row["proposal_id"]),
                path=str(row["path"]),
                before_content=(
                    bytes(row["before_content"])
                    if row["before_content"] is not None
                    else None
                ),
                before_sha256=(
                    str(row["before_sha256"])
                    if row["before_sha256"] is not None
                    else None
                ),
                after_sha256=str(row["after_sha256"]),
            )
            for row in rows
        )

    def finish_application(
        self,
        proposal_id: str,
        *,
        succeeded: bool,
        reason: str | None = None,
    ) -> StoredChangeProposal:
        return self._transition(
            proposal_id,
            expected_status="applying",
            next_status="applied" if succeeded else "apply_failed",
            reason=reason,
        )

    def start_rollback(self, proposal_id: str) -> StoredChangeProposal:
        return self._transition(
            proposal_id,
            expected_status="applied",
            next_status="rolling_back",
            reason=None,
        )

    def finish_rollback(
        self,
        proposal_id: str,
        *,
        succeeded: bool,
        reason: str | None = None,
    ) -> StoredChangeProposal:
        return self._transition(
            proposal_id,
            expected_status="rolling_back",
            next_status="rolled_back" if succeeded else "rollback_failed",
            reason=reason,
        )

    def _transition(
        self,
        proposal_id: str,
        *,
        expected_status: str,
        next_status: str,
        reason: str | None,
    ) -> StoredChangeProposal:
        normalized_reason = reason.strip() if reason else None
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM change_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
                if row is None:
                    raise ChangeProposalError("상태를 바꿀 수정안을 찾지 못했습니다.")
                current = _proposal_from_row(row)
                if current.status != expected_status:
                    raise ChangeProposalError(
                        f"수정안 상태가 {expected_status}일 때만 {next_status}로 바꿀 수 있습니다."
                    )
                now = datetime.now(UTC).isoformat()
                updated = connection.execute(
                    "UPDATE change_proposals SET status = ?, updated_at = ? "
                    "WHERE proposal_id = ? AND status = ?",
                    (next_status, now, proposal_id, expected_status),
                )
                if updated.rowcount != 1:
                    raise ChangeProposalError("수정안 상태가 동시에 변경되었습니다.")
                connection.execute(
                    "INSERT INTO change_proposal_events "
                    "(proposal_id, status, reason, created_at) VALUES (?, ?, ?, ?)",
                    (proposal_id, next_status, normalized_reason, now),
                )
                updated_row = connection.execute(
                    "SELECT * FROM change_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
        except ChangeProposalError:
            raise
        except sqlite3.Error as error:
            raise ChangeProposalError(f"수정안 상태 저장에 실패했습니다: {error}") from error
        if updated_row is None:
            raise ChangeProposalError("변경된 수정안 상태를 다시 읽지 못했습니다.")
        return _proposal_from_row(updated_row)

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS change_proposals (
                        proposal_id TEXT PRIMARY KEY,
                        authorization_id TEXT NOT NULL UNIQUE,
                        objective TEXT NOT NULL,
                        patch_sha256 TEXT NOT NULL,
                        result_json TEXT NOT NULL,
                        explanation_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS change_proposal_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        proposal_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        reason TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (proposal_id) REFERENCES change_proposals(proposal_id)
                    );
                    CREATE TABLE IF NOT EXISTS change_file_snapshots (
                        proposal_id TEXT NOT NULL,
                        path TEXT NOT NULL,
                        before_content BLOB,
                        before_sha256 TEXT,
                        after_sha256 TEXT NOT NULL,
                        PRIMARY KEY (proposal_id, path),
                        FOREIGN KEY (proposal_id) REFERENCES change_proposals(proposal_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_change_proposals_status_created
                    ON change_proposals(status, created_at);
                    """
                )
        except (OSError, sqlite3.Error) as error:
            raise ChangeProposalError(f"수정안 저장소 초기화에 실패했습니다: {error}") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class PersistentIsolatedWorkService:
    """Execute an approved isolated change and persist its Winter explanation."""

    def __init__(
        self,
        worker: IsolatedWorkExecutor,
        proposals: SqliteChangeProposalRepository,
    ) -> None:
        self._worker = worker
        self._proposals = proposals

    def execute(
        self, authorization: ImplementationAuthorization
    ) -> StoredChangeProposal:
        result = self._worker.execute(authorization)
        return self._proposals.record(authorization, result)


def _describe_changes(result: IsolatedWorkResult) -> tuple[str, ...]:
    patch_by_path = _patch_lines_by_path(result.patch)
    descriptions: list[str] = []
    for changed in result.changed_files:
        removed, added = patch_by_path.get(changed.path, ((), ()))
        if len(removed) == 1 and len(added) == 1:
            descriptions.append(
                f"{changed.path}에서 `{_code_excerpt(removed[0])}`를 "
                f"`{_code_excerpt(added[0])}`로 바꿨어."
            )
        elif changed.change_type == "added":
            descriptions.append(
                f"{changed.path} 파일을 새로 만들고 코드 {len(added)}줄을 추가했어."
            )
        else:
            descriptions.append(
                f"{changed.path}에서 코드 {len(removed)}줄을 제거하고 "
                f"{len(added)}줄을 추가했어."
            )
    return tuple(descriptions)


def _patch_lines_by_path(
    patch: str,
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    collected: dict[str, tuple[list[str], list[str]]] = {}
    current: str | None = None
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/") :]
            collected.setdefault(current, ([], []))
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            collected[current][1].append(line[1:].strip())
        elif current is not None and line.startswith("-") and not line.startswith("---"):
            collected[current][0].append(line[1:].strip())
    return {
        path: (tuple(lines[0]), tuple(lines[1]))
        for path, lines in collected.items()
    }


def _code_excerpt(value: str) -> str:
    normalized = " ".join(value.replace("`", "'").split())
    return normalized[:117] + "..." if len(normalized) > 120 else normalized


def _verification_label(status: str) -> str:
    return {"pass": "통과", "fail": "실패", "not_run": "실행 안 됨"}.get(
        status, "알 수 없음"
    )


def _patch_sha256(patch: str) -> str:
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def _proposal_values(proposal: StoredChangeProposal) -> tuple[object, ...]:
    return (
        proposal.proposal_id,
        proposal.authorization_id,
        proposal.objective,
        proposal.patch_sha256,
        json.dumps(asdict(proposal.result), ensure_ascii=False, sort_keys=True),
        json.dumps(asdict(proposal.explanation), ensure_ascii=False, sort_keys=True),
        proposal.status,
        proposal.created_at,
        proposal.updated_at,
    )


def _proposal_from_row(row: sqlite3.Row) -> StoredChangeProposal:
    raw = dict(row)
    try:
        result = _result_from_payload(json.loads(raw.pop("result_json")))
        explanation = _explanation_from_payload(
            json.loads(raw.pop("explanation_json"))
        )
        return StoredChangeProposal(result=result, explanation=explanation, **raw)
    except (json.JSONDecodeError, TypeError, KeyError, IsolatedWorkError) as error:
        raise ChangeProposalError("저장된 수정안 데이터가 손상되었습니다.") from error


def _validate_apply_snapshots(
    proposal: StoredChangeProposal,
    snapshots: tuple[ChangeFileSnapshot, ...],
) -> None:
    expected = {item.path: item for item in proposal.result.changed_files}
    actual = {item.path: item for item in snapshots}
    if len(actual) != len(snapshots) or set(actual) != set(expected):
        raise ChangeProposalError("적용 snapshot의 파일 범위가 수정안과 다릅니다.")
    for path, changed in expected.items():
        snapshot = actual[path]
        if snapshot.proposal_id != proposal.proposal_id:
            raise ChangeProposalError("적용 snapshot의 수정안 ID가 다릅니다.")
        if (
            snapshot.before_sha256 != changed.before_sha256
            or snapshot.after_sha256 != changed.after_sha256
        ):
            raise ChangeProposalError("적용 snapshot hash가 수정안과 다릅니다.")
        if snapshot.before_content is None:
            if changed.before_sha256 is not None:
                raise ChangeProposalError("수정 파일의 적용 전 내용이 없습니다.")
        elif hashlib.sha256(snapshot.before_content).hexdigest() != changed.before_sha256:
            raise ChangeProposalError("적용 전 내용의 hash가 수정안과 다릅니다.")


def _result_from_payload(payload: object) -> IsolatedWorkResult:
    if not isinstance(payload, dict):
        raise ChangeProposalError("저장된 Worker 결과는 object여야 합니다.")
    changed = tuple(ChangedFile(**item) for item in _dict_items(payload["changed_files"]))
    verifications = tuple(
        VerificationResult(**item) for item in _dict_items(payload["verifications"])
    )
    return IsolatedWorkResult(
        proposal_id=_text(payload["proposal_id"]),
        authorization_id=_text(payload["authorization_id"]),
        status=_text(payload["status"]),
        summary=_text(payload["summary"]),
        changed_files=changed,
        patch=_text(payload["patch"]),
        verifications=verifications,
        limitations=tuple(_string_items(payload["limitations"])),
        worker=_text(payload["worker"]),
        model=_text(payload["model"]),
    )


def _explanation_from_payload(payload: object) -> ChangeExplanation:
    if not isinstance(payload, dict):
        raise ChangeProposalError("저장된 수정 설명은 object여야 합니다.")
    return ChangeExplanation(
        proposal_id=_text(payload["proposal_id"]),
        headline=_text(payload["headline"]),
        purpose=_text(payload["purpose"]),
        changes=tuple(_string_items(payload["changes"])),
        verification=tuple(_string_items(payload["verification"])),
        risks=tuple(_string_items(payload["risks"])),
        worker_notes=tuple(_string_items(payload["worker_notes"])),
        recommendation=_text(payload["recommendation"]),
        adoption_question=(
            _text(payload["adoption_question"])
            if payload["adoption_question"] is not None
            else None
        ),
        message=_text(payload["message"]),
    )


def _dict_items(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ChangeProposalError("저장된 수정안 목록이 올바르지 않습니다.")
    return tuple(value)


def _string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ChangeProposalError("저장된 문자열 목록이 올바르지 않습니다.")
    return tuple(value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ChangeProposalError("저장된 수정안 문자열이 올바르지 않습니다.")
    return value


def _proposal_identity(proposal: StoredChangeProposal) -> tuple[object, ...]:
    return (
        proposal.proposal_id,
        proposal.authorization_id,
        proposal.objective,
        proposal.patch_sha256,
        proposal.result,
        proposal.explanation,
    )
