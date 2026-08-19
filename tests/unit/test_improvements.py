from pathlib import Path
import sqlite3

import pytest

from companion.capabilities import SqliteCapabilityRegistry
from companion.improvements import (
    ImprovementLedgerError,
    PersistentImprovementSnapshot,
    SqliteImprovementLedger,
)


def test_schema_v1_migrates_to_cycle_records(tmp_path: Path) -> None:
    path = tmp_path / "improvements.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE improvement_schema (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO improvement_schema VALUES (1)")

    SqliteImprovementLedger(path)

    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT version FROM improvement_schema"
        ).fetchone()
        cycle_table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'improvement_cycle_records'"
        ).fetchone()
    assert version == (2,)
    assert cycle_table == ("improvement_cycle_records",)


def _observe(ledger: SqliteImprovementLedger):
    return ledger.observe(
        title="대답 속도 개선",
        problem="첫 답변이 늦어서 실시간 대화가 끊긴다",
        evidence=("conversation:17", "latency:voice:4"),
    )


def _propose(ledger: SqliteImprovementLedger, improvement_id: str):
    return ledger.propose(
        improvement_id,
        why_it_matters="대화의 호흡을 유지해야 한다",
        solution="첫 문장을 먼저 합성한다",
        alternatives=("전체 답변을 기다린다", "텍스트만 먼저 표시한다"),
        expected_effect="첫 소리 대기시간이 줄어든다",
        risks=("문장 경계가 부자연스러울 수 있다",),
        privacy_impact="새 외부 전송 없음",
        implementation_scope=("src/companion/web.py",),
        acceptance_criteria=("first audio p50 3초 이하", "기존 전체 회귀 통과"),
        rollback_plan="이전 문장 합성 경로로 되돌린다",
    )


def test_ledger_persists_problem_proposal_and_explicit_approval(
    tmp_path: Path,
) -> None:
    path = tmp_path / "improvements.sqlite"
    ledger = SqliteImprovementLedger(path)
    observed = _observe(ledger)
    ledger.start_discussion(observed.id)
    proposed = _propose(ledger, observed.id)
    proposal = proposed.current_proposal
    assert proposal is not None

    approved = ledger.approve_implementation(
        observed.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        decided_by="cheonu",
        evidence="conversation:approval:23",
    )
    reopened = SqliteImprovementLedger(path).get(observed.id)

    assert approved.status == "approved"
    assert reopened.status == "approved"
    assert reopened.evidence == ("conversation:17", "latency:voice:4")
    assert reopened.current_proposal == proposal
    assert [decision.kind for decision in reopened.decisions] == [
        "implementation_approval"
    ]
    assert [event.to_status for event in reopened.transitions] == [
        "discussing",
        "proposed",
        "approved",
    ]


def test_proposal_requires_all_fields_and_cannot_skip_discussion(
    tmp_path: Path,
) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item = _observe(ledger)

    with pytest.raises(ImprovementLedgerError, match="discussing"):
        _propose(ledger, item.id)

    ledger.start_discussion(item.id)
    with pytest.raises(ImprovementLedgerError, match="acceptance criteria"):
        ledger.propose(
            item.id,
            why_it_matters="중요하다",
            solution="고친다",
            alternatives=("유지",),
            expected_effect="개선된다",
            risks=("회귀",),
            privacy_impact="없음",
            implementation_scope=("src/example.py",),
            acceptance_criteria=(),
            rollback_plan="되돌린다",
        )


def test_observation_and_approval_require_stable_evidence_references(
    tmp_path: Path,
) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    with pytest.raises(ImprovementLedgerError, match="stable references"):
        ledger.observe(
            title="문제",
            problem="근거 없는 문제",
            evidence=("사용자가 그랬음",),
        )

    item = _observe(ledger)
    ledger.start_discussion(item.id)
    proposal = _propose(ledger, item.id).current_proposal
    assert proposal is not None
    with pytest.raises(ImprovementLedgerError, match="stable reference"):
        ledger.approve_implementation(
            item.id,
            proposal_version=proposal.version,
            proposal_hash=proposal.proposal_hash,
            decided_by="cheonu",
            evidence="천우가 승인함",
        )


def test_approval_is_bound_to_current_version_and_hash(tmp_path: Path) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item = _observe(ledger)
    ledger.start_discussion(item.id)
    proposed = _propose(ledger, item.id)
    proposal = proposed.current_proposal
    assert proposal is not None

    with pytest.raises(ImprovementLedgerError, match="version"):
        ledger.approve_implementation(
            item.id,
            proposal_version=proposal.version + 1,
            proposal_hash=proposal.proposal_hash,
            decided_by="cheonu",
            evidence="conversation:approval:23",
        )
    with pytest.raises(ImprovementLedgerError, match="hash"):
        ledger.approve_implementation(
            item.id,
            proposal_version=proposal.version,
            proposal_hash="wrong",
            decided_by="cheonu",
            evidence="conversation:approval:23",
        )


def test_revision_retains_old_proposal_and_requires_new_approval(
    tmp_path: Path,
) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item = _observe(ledger)
    ledger.start_discussion(item.id)
    first = _propose(ledger, item.id).current_proposal
    assert first is not None

    ledger.request_revision(
        item.id,
        proposal_version=first.version,
        proposal_hash=first.proposal_hash,
        reason="scope를 더 줄여야 한다",
    )
    second_item = _propose(ledger, item.id)
    second = second_item.current_proposal
    assert second is not None

    assert second.version == 2
    assert second.proposal_hash != first.proposal_hash
    assert [proposal.version for proposal in second_item.proposals] == [1, 2]
    assert second_item.decisions == ()
    assert second_item.transitions[-2].reason == "scope를 더 줄여야 한다"


def test_invalid_or_duplicate_decisions_fail_closed(tmp_path: Path) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item = _observe(ledger)

    with pytest.raises(ImprovementLedgerError, match="cannot transition"):
        ledger.start_discussion(ledger.start_discussion(item.id).id)

    proposed = _propose(ledger, item.id)
    proposal = proposed.current_proposal
    assert proposal is not None
    ledger.approve_implementation(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        decided_by="cheonu",
        evidence="conversation:approval:23",
    )
    with pytest.raises(ImprovementLedgerError, match="requires a proposed"):
        ledger.approve_implementation(
            item.id,
            proposal_version=proposal.version,
            proposal_hash=proposal.proposal_hash,
            decided_by="cheonu",
            evidence="conversation:approval:23",
        )


def test_rejection_is_terminal_and_hidden_from_active_snapshot(tmp_path: Path) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item = _observe(ledger)

    rejected = ledger.reject(item.id, reason="현재는 우선순위가 아니다")
    snapshot = PersistentImprovementSnapshot(ledger).snapshot()

    assert rejected.status == "rejected"
    assert rejected.transitions[-1].reason == "현재는 우선순위가 아니다"
    assert snapshot.items == ()
    with pytest.raises(ImprovementLedgerError, match="cannot transition"):
        ledger.start_discussion(item.id)


def test_implementation_approval_does_not_adopt_a_capability(tmp_path: Path) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    registry = SqliteCapabilityRegistry(tmp_path / "capabilities.sqlite")
    item = _observe(ledger)
    ledger.start_discussion(item.id)
    proposal = _propose(ledger, item.id).current_proposal
    assert proposal is not None

    approved = ledger.approve_implementation(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        decided_by="cheonu",
        evidence="conversation:approval:23",
    )

    assert approved.status == "approved"
    assert registry.list() == ()


def test_snapshot_is_read_only_and_marks_implementation_only(tmp_path: Path) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item = _observe(ledger)
    ledger.start_discussion(item.id)
    proposal = _propose(ledger, item.id).current_proposal
    assert proposal is not None
    ledger.approve_implementation(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        decided_by="cheonu",
        evidence="conversation:approval:23",
    )

    before = ledger.get(item.id)
    snapshot = PersistentImprovementSnapshot(ledger).snapshot()
    after = ledger.get(item.id)

    assert before == after
    assert snapshot.items[0].implementation_approved is True
    message = snapshot.system_message()
    assert "구현만 승인됨" in message
    assert "Worker를 실행하지 마" in message
    assert "채택 승인" in message
