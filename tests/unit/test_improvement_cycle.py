from pathlib import Path

import pytest

from companion.improvement_cycle import (
    FakeImprovementWorker,
    FakeIndependentVerifier,
    FakeWorkerLifecycle,
    ValidationReport,
    WorkerResult,
)
from companion.improvements import ImprovementLedgerError, SqliteImprovementLedger


def _proposed(ledger: SqliteImprovementLedger):
    item = ledger.observe(
        title="첫 응답 속도 개선",
        problem="첫 음성이 늦어 대화 흐름이 끊긴다",
        evidence=("conversation:latency:17", "latency:voice:4"),
    )
    ledger.start_discussion(item.id)
    proposed = ledger.propose(
        item.id,
        why_it_matters="대화 호흡을 유지해야 한다",
        solution="첫 문장을 먼저 합성한다",
        alternatives=("전체 답변을 기다린다",),
        expected_effect="첫 소리 대기시간이 줄어든다",
        risks=("문장 경계가 어색할 수 있다",),
        privacy_impact="새 외부 전송 없음",
        implementation_scope=("src/companion/web.py",),
        acceptance_criteria=("first audio p50 3초 이하",),
        rollback_plan="이전 합성 경로로 되돌린다",
    )
    proposal = proposed.current_proposal
    assert proposal is not None
    return proposed, proposal


def _approved(ledger: SqliteImprovementLedger):
    item, proposal = _proposed(ledger)
    ledger.approve_implementation(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        decided_by="cheonu",
        evidence="conversation:implementation-approval:23",
    )
    return item, proposal


def _worker() -> FakeImprovementWorker:
    return FakeImprovementWorker(
        WorkerResult(
            succeeded=True,
            summary="격리된 fake artifact를 만들었다",
            evidence=("test:fake-worker:passed",),
            artifact_id="fake-artifact-1",
            artifact_hash="a" * 64,
        )
    )


def _verifier(*, passed: bool = True) -> FakeIndependentVerifier:
    return FakeIndependentVerifier(
        ValidationReport(
            passed=passed,
            summary="acceptance를 독립 확인했다" if passed else "latency gate 실패",
            checks=("scope", "acceptance", "rollback"),
            evidence=(
                ("test:fake-verifier:passed",)
                if passed
                else ("test:fake-verifier:failed",)
            ),
        )
    )


def _approve_adoption(
    ledger: SqliteImprovementLedger,
    improvement_id: str,
    *,
    proposal_version: int,
    proposal_hash: str,
    validation_report_hash: str,
):
    return ledger.approve_adoption(
        improvement_id,
        proposal_version=proposal_version,
        proposal_hash=proposal_hash,
        validation_report_hash=validation_report_hash,
        change_proposal_id="fake-artifact-1",
        patch_sha256="a" * 64,
        project_baseline_sha256="b" * 64,
        capability_spec_sha256="c" * 64,
        decided_by="cheonu",
        evidence="conversation:adoption-approval:31",
    )


def test_worker_is_never_called_without_exact_implementation_approval(
    tmp_path: Path,
) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item, proposal = _proposed(ledger)
    worker = _worker()
    cycle = FakeWorkerLifecycle(ledger, worker, _verifier())

    with pytest.raises(ImprovementLedgerError, match="requires status approved"):
        cycle.run_worker(
            item.id,
            proposal_version=proposal.version,
            proposal_hash=proposal.proposal_hash,
        )
    assert worker.calls == 0

    ledger.approve_implementation(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        decided_by="cheonu",
        evidence="conversation:implementation-approval:23",
    )
    with pytest.raises(ImprovementLedgerError, match="hash"):
        cycle.run_worker(
            item.id,
            proposal_version=proposal.version,
            proposal_hash="wrong-hash",
        )
    assert worker.calls == 0


def test_worker_success_requires_independent_validation_and_adoption(
    tmp_path: Path,
) -> None:
    path = tmp_path / "improvements.sqlite"
    ledger = SqliteImprovementLedger(path)
    item, proposal = _approved(ledger)
    worker = _worker()
    verifier = _verifier()
    cycle = FakeWorkerLifecycle(ledger, worker, verifier)

    after_worker = cycle.run_worker(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    assert after_worker.status == "validating"
    assert worker.calls == 1
    assert verifier.calls == 0
    assert [decision.kind for decision in after_worker.decisions] == [
        "implementation_approval"
    ]

    reopened = SqliteImprovementLedger(path)
    after_validation = FakeWorkerLifecycle(reopened, _worker(), verifier).validate(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    assert after_validation.status == "ready_to_adopt"
    assert verifier.calls == 1
    assert [decision.kind for decision in after_validation.decisions] == [
        "implementation_approval"
    ]

    report = reopened.latest_cycle_record(item.id, "validation_report")
    assert report is not None
    with pytest.raises(ImprovementLedgerError, match="validation report"):
        _approve_adoption(
            reopened,
            item.id,
            proposal_version=proposal.version,
            proposal_hash=proposal.proposal_hash,
            validation_report_hash="wrong-report-hash",
        )
    approved = _approve_adoption(
        reopened,
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        validation_report_hash=report.record_hash,
    )
    assert approved.status == "adoption_approved"
    assert [decision.kind for decision in approved.decisions] == [
        "implementation_approval",
        "adoption_approval",
    ]
    assert reopened.latest_cycle_record(item.id, "adoption_completion") is None


def test_adopted_cycle_persists_and_only_then_allows_rollback(tmp_path: Path) -> None:
    path = tmp_path / "improvements.sqlite"
    ledger = SqliteImprovementLedger(path)
    item, proposal = _approved(ledger)

    with pytest.raises(ImprovementLedgerError, match="completed adoption"):
        ledger.rollback_adoption(
            item.id,
            proposal_version=proposal.version,
            proposal_hash=proposal.proposal_hash,
            reason="아직 채택 전",
            evidence=("test:premature-rollback",),
        )

    cycle = FakeWorkerLifecycle(ledger, _worker(), _verifier())
    cycle.run_worker(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    cycle.validate(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    report = ledger.latest_cycle_record(item.id, "validation_report")
    assert report is not None
    _approve_adoption(
        ledger,
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        validation_report_hash=report.record_hash,
    )
    approval = ledger.latest_cycle_record(item.id, "adoption_approval")
    assert approval is not None
    ledger.complete_adoption(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        adoption_approval_hash=approval.record_hash,
        source_reference="change_proposal:fake-artifact-1:applied",
        capability_reference="test:fake-capability:available",
    )

    reopened = SqliteImprovementLedger(path)
    rolled_back = reopened.rollback_adoption(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        reason="실사용 회귀 발견",
        evidence=("outcome:voice-regression:1",),
    )
    final = SqliteImprovementLedger(path).get(item.id)
    assert rolled_back.status == "rolled_back"
    assert final.status == "rolled_back"
    assert final.cycle_records[-1].kind == "rollback"
    assert final.cycle_records[-1].payload["reason"] == "실사용 회귀 발견"
    assert [event.to_status for event in final.transitions][-6:] == [
        "implementing",
        "validating",
        "ready_to_adopt",
        "adoption_approved",
        "adopted",
        "rolled_back",
    ]
    with pytest.raises(ImprovementLedgerError):
        reopened.rollback_adoption(
            item.id,
            proposal_version=proposal.version,
            proposal_hash=proposal.proposal_hash,
            reason="중복",
            evidence=("outcome:duplicate:1",),
        )


def test_failed_independent_validation_cannot_be_adopted(tmp_path: Path) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item, proposal = _approved(ledger)
    cycle = FakeWorkerLifecycle(ledger, _worker(), _verifier(passed=False))
    cycle.run_worker(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    failed = cycle.validate(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    report = ledger.latest_cycle_record(item.id, "validation_report")
    assert report is not None
    assert failed.status == "validation_failed"
    with pytest.raises(ImprovementLedgerError):
        _approve_adoption(
            ledger,
            item.id,
            proposal_version=proposal.version,
            proposal_hash=proposal.proposal_hash,
            validation_report_hash=report.record_hash,
        )
    revised = ledger.request_revision(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        reason="검증 실패를 반영해 scope를 다시 논의한다",
    )
    assert revised.status == "discussing"


def test_worker_failure_stops_before_verification(tmp_path: Path) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item, proposal = _approved(ledger)
    worker = FakeImprovementWorker(
        WorkerResult(
            succeeded=False,
            summary="격리 구현 실패",
            evidence=("test:fake-worker:failed",),
            artifact_id="fake-artifact-failed",
            artifact_hash="d" * 64,
        )
    )
    verifier = _verifier()
    failed = FakeWorkerLifecycle(ledger, worker, verifier).run_worker(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )

    assert failed.status == "validation_failed"
    assert worker.calls == 1
    assert verifier.calls == 0
    assert failed.cycle_records[-1].payload["succeeded"] is False
