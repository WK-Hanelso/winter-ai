from pathlib import Path

import pytest

from companion.change_proposal import SqliteChangeProposalRepository
from companion.improvements import ImprovementLedgerError, SqliteImprovementLedger
from companion.isolated_lifecycle_bridge import (
    IsolatedLifecycleBridge,
    IsolatedLifecycleBridgeError,
    build_implementation_authorization,
)
from companion.isolated_work import (
    ChangedFile,
    ImplementationAuthorization,
    IsolatedWorkResult,
    VerificationResult,
)


class Worker:
    def __init__(
        self,
        *,
        verification_passed: bool = True,
        changed_path: str = "src/calc.py",
    ) -> None:
        self.verification_passed = verification_passed
        self.changed_path = changed_path
        self.calls = 0

    def execute(
        self, authorization: ImplementationAuthorization
    ) -> IsolatedWorkResult:
        self.calls += 1
        return _result(
            authorization,
            verification_passed=self.verification_passed,
            changed_path=self.changed_path,
        )


def _result(
    authorization: ImplementationAuthorization,
    *,
    verification_passed: bool = True,
    changed_path: str = "src/calc.py",
) -> IsolatedWorkResult:
    status = "pass" if verification_passed else "fail"
    return IsolatedWorkResult(
        proposal_id="proposal-linked-1",
        authorization_id=authorization.authorization_id,
        status="awaiting_adoption" if verification_passed else "verification_failed",
        summary="untrusted Worker summary",
        changed_files=(
            ChangedFile(
                path=changed_path,
                change_type="modified",
                before_sha256="a" * 64,
                after_sha256="b" * 64,
            ),
        ),
        patch=(
            f"--- a/{changed_path}\n"
            f"+++ b/{changed_path}\n"
            "@@ -1 +1 @@\n"
            "-return left - right\n"
            "+return left + right\n"
        ),
        verifications=(
            VerificationResult(
                name="python_unittest",
                status=status,
                exit_code=0 if verification_passed else 1,
                output="OK" if verification_passed else "FAILED",
            ),
            VerificationResult(
                name="python_structure",
                status="pass",
                exit_code=0,
                output="OK",
            ),
        ),
        limitations=("full repository validation not run",),
        worker="isolated-test-worker",
        model="fake",
    )


def _proposed(ledger: SqliteImprovementLedger):
    item = ledger.observe(
        title="계산 오류 수정",
        problem="add 함수가 뺄셈을 한다",
        evidence=("test:calc-regression",),
    )
    ledger.start_discussion(item.id)
    proposed = ledger.propose(
        item.id,
        why_it_matters="계산 결과가 틀리다",
        solution="add 함수가 덧셈을 반환하도록 수정한다",
        alternatives=("호출부에서 보정",),
        expected_effect="정확한 합계를 반환한다",
        risks=("다른 계산 회귀",),
        privacy_impact="없음",
        implementation_scope=("src/calc.py",),
        acceptance_criteria=("unit test 통과",),
        rollback_plan="이전 함수를 복원한다",
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
        evidence="conversation:implementation-approval:122",
    )
    return item, proposal


def test_bridge_never_calls_worker_without_exact_current_approval(
    tmp_path: Path,
) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item, proposal = _proposed(ledger)
    worker = Worker()
    bridge = IsolatedLifecycleBridge(
        ledger,
        SqliteChangeProposalRepository(tmp_path / "changes.sqlite"),
        worker,
    )

    with pytest.raises(IsolatedLifecycleBridgeError, match="requires approved"):
        bridge.execute(
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
        evidence="conversation:implementation-approval:122",
    )
    with pytest.raises(IsolatedLifecycleBridgeError, match="hash"):
        bridge.execute(
            item.id,
            proposal_version=proposal.version,
            proposal_hash="wrong",
        )
    assert worker.calls == 0


def test_bridge_persists_identity_validates_and_never_applies_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "src" / "calc.py"
    source.parent.mkdir(parents=True)
    source.write_text("return left - right\n", encoding="utf-8")
    original = source.read_bytes()
    improvement_path = tmp_path / "improvements.sqlite"
    change_path = tmp_path / "changes.sqlite"
    ledger = SqliteImprovementLedger(improvement_path)
    item, proposal = _approved(ledger)
    worker = Worker()
    changes = SqliteChangeProposalRepository(change_path)
    bridge = IsolatedLifecycleBridge(ledger, changes, worker)

    after_worker = bridge.execute(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    assert after_worker.status == "validating"
    assert worker.calls == 1
    worker_record = after_worker.cycle_records[-1]
    stored = changes.get(str(worker_record.payload["artifact_id"]))
    assert stored is not None
    assert worker_record.payload["artifact_hash"] == stored.patch_sha256
    assert source.read_bytes() == original

    reopened = IsolatedLifecycleBridge(
        SqliteImprovementLedger(improvement_path),
        SqliteChangeProposalRepository(change_path),
        Worker(),
    )
    ready = reopened.validate(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    assert ready.status == "ready_to_adopt"
    report = SqliteImprovementLedger(improvement_path).latest_cycle_record(
        item.id, "validation_report"
    )
    assert report is not None
    adopted = SqliteImprovementLedger(improvement_path).approve_adoption(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        validation_report_hash=report.record_hash,
        decided_by="cheonu",
        evidence="conversation:adoption-approval:122",
    )
    assert adopted.status == "adopted"
    assert SqliteChangeProposalRepository(change_path).get(stored.proposal_id) == stored
    assert source.read_bytes() == original


def test_restart_reconciles_persisted_isolated_result_without_second_call(
    tmp_path: Path,
) -> None:
    improvement_path = tmp_path / "improvements.sqlite"
    change_path = tmp_path / "changes.sqlite"
    ledger = SqliteImprovementLedger(improvement_path)
    item, proposal = _approved(ledger)
    authorization = build_implementation_authorization(proposal)
    ledger.begin_implementation(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    SqliteChangeProposalRepository(change_path).record(
        authorization, _result(authorization)
    )
    worker = Worker()

    reconciled = IsolatedLifecycleBridge(
        SqliteImprovementLedger(improvement_path),
        SqliteChangeProposalRepository(change_path),
        worker,
    ).execute(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )

    assert reconciled.status == "validating"
    assert worker.calls == 0
    assert reconciled.cycle_records[-1].payload["artifact_id"] == "proposal-linked-1"


def test_restart_without_persisted_result_fails_closed_without_reexecution(
    tmp_path: Path,
) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item, proposal = _approved(ledger)
    ledger.begin_implementation(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    worker = Worker()
    bridge = IsolatedLifecycleBridge(
        ledger,
        SqliteChangeProposalRepository(tmp_path / "changes.sqlite"),
        worker,
    )

    with pytest.raises(IsolatedLifecycleBridgeError, match="manual recovery"):
        bridge.execute(
            item.id,
            proposal_version=proposal.version,
            proposal_hash=proposal.proposal_hash,
        )
    assert worker.calls == 0
    assert ledger.get(item.id).status == "implementing"


def test_failed_host_verification_cannot_reach_adoption(tmp_path: Path) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item, proposal = _approved(ledger)
    bridge = IsolatedLifecycleBridge(
        ledger,
        SqliteChangeProposalRepository(tmp_path / "changes.sqlite"),
        Worker(verification_passed=False),
    )
    bridge.execute(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    failed = bridge.validate(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )

    assert failed.status == "validation_failed"
    with pytest.raises(ImprovementLedgerError):
        ledger.approve_adoption(
            item.id,
            proposal_version=proposal.version,
            proposal_hash=proposal.proposal_hash,
            validation_report_hash="not-adoptable",
            decided_by="cheonu",
            evidence="conversation:adoption-approval:122",
        )


def test_scope_violation_is_persisted_as_failure_without_change_proposal(
    tmp_path: Path,
) -> None:
    ledger = SqliteImprovementLedger(tmp_path / "improvements.sqlite")
    item, proposal = _approved(ledger)
    changes = SqliteChangeProposalRepository(tmp_path / "changes.sqlite")
    worker = Worker(changed_path="src/outside.py")
    failed = IsolatedLifecycleBridge(ledger, changes, worker).execute(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )

    assert worker.calls == 1
    assert failed.status == "validation_failed"
    authorization = build_implementation_authorization(proposal)
    assert changes.get_by_authorization_id(authorization.authorization_id) is None
