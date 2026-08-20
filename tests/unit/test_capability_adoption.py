from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

import pytest

from companion.capabilities import SqliteCapabilityRegistry
from companion.capability_adoption import (
    CapabilityAdoptionError,
    CapabilitySpec,
    ExactCapabilityAdoption,
)
from companion.change_adoption import ChangeAdoptionService
from companion.change_proposal import SqliteChangeProposalRepository
from companion.improvements import SqliteImprovementLedger
from companion.isolated_lifecycle_bridge import IsolatedLifecycleBridge
from companion.isolated_work import (
    ChangedFile,
    ImplementationAuthorization,
    IsolatedWorkResult,
    VerificationResult,
)
from companion.project_baseline import build_project_baseline


class _Worker:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.calls = 0

    def execute(self, authorization: ImplementationAuthorization) -> IsolatedWorkResult:
        self.calls += 1
        before = b"return left - right\n"
        after = b"return left + right\n"
        return IsolatedWorkResult(
            proposal_id="proposal-exact-cycle",
            authorization_id=authorization.authorization_id,
            status="awaiting_adoption",
            summary="synthetic add fix",
            changed_files=(
                ChangedFile(
                    path="src/calc.py",
                    change_type="modified",
                    before_sha256=_sha(before),
                    after_sha256=_sha(after),
                ),
            ),
            patch=(
                "--- a/src/calc.py\n"
                "+++ b/src/calc.py\n"
                "@@ -1 +1 @@\n"
                "-return left - right\n"
                "+return left + right\n"
            ),
            verifications=(
                VerificationResult("python_unittest", "pass", 0, "OK"),
                VerificationResult("python_structure", "pass", 0, "OK"),
            ),
            limitations=("synthetic fixture only",),
            worker="deterministic-isolated-worker",
            model="fake",
            project_baseline=build_project_baseline(self.source),
        )


class _RepositoryVerifier:
    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed
        self.calls = 0

    def verify(self, source_root: Path) -> tuple[VerificationResult, ...]:
        self.calls += 1
        assert source_root.is_dir()
        return (
            VerificationResult(
                "full_repository",
                "pass" if self.passed else "fail",
                0 if self.passed else 1,
                "OK" if self.passed else "FAILED",
            ),
        )


@dataclass
class _Cycle:
    source: Path
    target: Path
    improvement_path: Path
    change_path: Path
    capability_path: Path
    improvement_id: str
    proposal_version: int
    proposal_hash: str
    validation_hash: str
    worker: _Worker


def _ready_cycle(tmp_path: Path) -> _Cycle:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    improvement_path = tmp_path / "improvements.sqlite"
    change_path = tmp_path / "changes.sqlite"
    capability_path = tmp_path / "capabilities.sqlite"
    ledger = SqliteImprovementLedger(improvement_path)
    item = ledger.observe(
        title="계산 capability 수정",
        problem="add가 뺄셈을 반환한다",
        evidence=("test:exact-cycle:regression",),
    )
    ledger.start_discussion(item.id)
    proposed = ledger.propose(
        item.id,
        why_it_matters="실제 계산 결과가 틀리다",
        solution="add가 덧셈을 반환하도록 수정한다",
        alternatives=("호출부 보정",),
        expected_effect="정확한 합계를 반환한다",
        risks=("계산 회귀",),
        privacy_impact="새 외부 전송 없음",
        implementation_scope=("src/calc.py",),
        acceptance_criteria=("독립 test 통과",),
        rollback_plan="before bytes 복원",
    )
    proposal = proposed.current_proposal
    assert proposal is not None
    ledger.approve_implementation(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        decided_by="cheonu",
        evidence="conversation:implementation-approval:124",
    )
    worker = _Worker(source)
    bridge = IsolatedLifecycleBridge(
        ledger, SqliteChangeProposalRepository(change_path), worker
    )
    bridge.execute(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    ready = bridge.validate(
        item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
    )
    assert ready.status == "ready_to_adopt"
    validation = ledger.latest_cycle_record(item.id, "validation_report")
    assert validation is not None
    return _Cycle(
        source=source,
        target=target,
        improvement_path=improvement_path,
        change_path=change_path,
        capability_path=capability_path,
        improvement_id=item.id,
        proposal_version=proposal.version,
        proposal_hash=proposal.proposal_hash,
        validation_hash=validation.record_hash,
        worker=worker,
    )


def _spec(*, version: str = "1") -> CapabilitySpec:
    return CapabilitySpec(
        id="synthetic-calculator",
        name="합계 계산",
        version=version,
        supports=("두 정수의 합을 계산한다",),
        limitations=("synthetic fixture에서만 검증됨",),
        claim_scopes=("synthetic.calculate",),
        dependencies=(),
        health_check="synthetic:full-repository",
    )


def _coordinator(
    cycle: _Cycle, *, verifier: _RepositoryVerifier | None = None
) -> ExactCapabilityAdoption:
    proposals = SqliteChangeProposalRepository(cycle.change_path)
    return ExactCapabilityAdoption(
        SqliteImprovementLedger(cycle.improvement_path),
        proposals,
        ChangeAdoptionService(
            cycle.source,
            proposals,
            repository_verifier=verifier or _RepositoryVerifier(),
        ),
        SqliteCapabilityRegistry(cycle.capability_path),
    )


def _approve(cycle: _Cycle, coordinator: ExactCapabilityAdoption) -> str:
    approved = coordinator.approve(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        validation_report_hash=cycle.validation_hash,
        capability=_spec(),
        decided_by="cheonu",
        evidence="conversation:adoption-approval:124",
    )
    assert approved.status == "adoption_approved"
    approval = SqliteImprovementLedger(cycle.improvement_path).latest_cycle_record(
        cycle.improvement_id, "adoption_approval"
    )
    assert approval is not None
    return approval.record_hash


def test_one_approval_reaches_source_capability_and_adopted_idempotently(
    tmp_path: Path,
) -> None:
    cycle = _ready_cycle(tmp_path)
    coordinator = _coordinator(cycle)
    approval_hash = _approve(cycle, coordinator)

    assert cycle.target.read_text(encoding="utf-8") == "return left - right\n"
    assert SqliteCapabilityRegistry(cycle.capability_path).list() == ()

    completed = coordinator.complete(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        adoption_approval_hash=approval_hash,
        capability=_spec(),
        observed_at="2026-08-20T00:00:00+00:00",
    )
    repeated = _coordinator(cycle).complete(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        adoption_approval_hash=approval_hash,
        capability=_spec(),
        observed_at="2026-08-20T00:00:00+00:00",
    )

    capability = SqliteCapabilityRegistry(cycle.capability_path).get(
        "synthetic-calculator"
    )
    assert completed.improvement_status == "adopted"
    assert repeated.improvement_status == "adopted"
    assert cycle.target.read_text(encoding="utf-8") == "return left + right\n"
    assert capability.status == "available"
    assert [(item.kind, item.passed) for item in capability.evidence] == [
        ("adoption", True),
        ("health", True),
        ("test", True),
    ]
    assert cycle.worker.calls == 1
    with pytest.raises(CapabilityAdoptionError, match="binding differs"):
        _coordinator(cycle).complete(
            cycle.improvement_id,
            proposal_version=cycle.proposal_version,
            proposal_hash=cycle.proposal_hash,
            adoption_approval_hash=approval_hash,
            capability=_spec(version="2"),
            observed_at="2026-08-20T00:00:00+00:00",
        )


def test_exact_capability_binding_mismatch_fails_before_source_write(
    tmp_path: Path,
) -> None:
    with pytest.raises(CapabilityAdoptionError, match="incomplete"):
        CapabilitySpec(
            id=" synthetic-calculator",
            name="합계 계산",
            version="1",
            supports=("두 정수의 합을 계산한다",),
            limitations=("synthetic fixture에서만 검증됨",),
            claim_scopes=("synthetic.calculate",),
            dependencies=(),
            health_check="synthetic:full-repository",
        )
    cycle = _ready_cycle(tmp_path)
    coordinator = _coordinator(cycle)
    approval_hash = _approve(cycle, coordinator)

    with pytest.raises(CapabilityAdoptionError, match="binding differs"):
        coordinator.complete(
            cycle.improvement_id,
            proposal_version=cycle.proposal_version,
            proposal_hash=cycle.proposal_hash,
            adoption_approval_hash=approval_hash,
            capability=_spec(version="2"),
            observed_at="2026-08-20T00:00:00+00:00",
        )

    assert cycle.target.read_text(encoding="utf-8") == "return left - right\n"
    assert SqliteImprovementLedger(cycle.improvement_path).get(
        cycle.improvement_id
    ).status == "adoption_approved"
    assert SqliteCapabilityRegistry(cycle.capability_path).list() == ()


def test_repository_verification_failure_restores_and_never_activates(
    tmp_path: Path,
) -> None:
    cycle = _ready_cycle(tmp_path)
    verifier = _RepositoryVerifier(passed=False)
    coordinator = _coordinator(cycle, verifier=verifier)
    approval_hash = _approve(cycle, coordinator)

    with pytest.raises(CapabilityAdoptionError, match="failed"):
        coordinator.complete(
            cycle.improvement_id,
            proposal_version=cycle.proposal_version,
            proposal_hash=cycle.proposal_hash,
            adoption_approval_hash=approval_hash,
            capability=_spec(),
            observed_at="2026-08-20T00:00:00+00:00",
        )

    assert verifier.calls == 1
    assert cycle.target.read_text(encoding="utf-8") == "return left - right\n"
    assert SqliteImprovementLedger(cycle.improvement_path).get(
        cycle.improvement_id
    ).status == "adoption_failed"
    assert SqliteCapabilityRegistry(cycle.capability_path).list() == ()
    stored = SqliteChangeProposalRepository(cycle.change_path).get(
        "proposal-exact-cycle"
    )
    assert stored is not None and stored.status == "apply_failed"
    failed = SqliteImprovementLedger(cycle.improvement_path)
    revised = failed.request_revision(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        reason="repository verifier 실패를 반영해 다시 논의한다",
    )
    assert revised.status == "discussing"


def test_restart_resumes_after_source_apply_and_partial_registry(
    tmp_path: Path,
) -> None:
    cycle = _ready_cycle(tmp_path)
    coordinator = _coordinator(cycle)
    approval_hash = _approve(cycle, coordinator)
    proposals = SqliteChangeProposalRepository(cycle.change_path)
    proposals.record_decision(
        "proposal-exact-cycle",
        decision="adopt",
        expected_patch_sha256=proposals.get("proposal-exact-cycle").patch_sha256,  # type: ignore[union-attr]
    )
    ChangeAdoptionService(
        cycle.source,
        proposals,
        repository_verifier=_RepositoryVerifier(),
    ).apply(
        "proposal-exact-cycle",
        expected_patch_sha256=proposals.get("proposal-exact-cycle").patch_sha256,  # type: ignore[union-attr]
    )
    registry = SqliteCapabilityRegistry(cycle.capability_path)
    registry.propose(
        capability_id=_spec().id,
        name=_spec().name,
        version=_spec().version,
        supports=_spec().supports,
        limitations=_spec().limitations,
        claim_scopes=_spec().claim_scopes,
        dependencies=_spec().dependencies,
        health_check=_spec().health_check,
        adopted_change=(
            f"improvement:{cycle.improvement_id}:v{cycle.proposal_version}:"
            f"{cycle.proposal_hash}"
        ),
        rollback_target=(
            "change_proposal:proposal-exact-cycle:"
            f"{proposals.get('proposal-exact-cycle').patch_sha256}"  # type: ignore[union-attr]
        ),
    )
    registry.transition(_spec().id, "approved")
    registry.transition(_spec().id, "building")
    registry.transition(_spec().id, "testing")

    completed = _coordinator(cycle).complete(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        adoption_approval_hash=approval_hash,
        capability=_spec(),
        observed_at="2026-08-20T00:00:00+00:00",
    )

    assert completed.improvement_status == "adopted"
    assert completed.source_status == "applied"
    assert completed.capability_status == "available"
    assert cycle.worker.calls == 1


def test_exact_cycle_rollback_is_persistent_and_idempotent(tmp_path: Path) -> None:
    cycle = _ready_cycle(tmp_path)
    coordinator = _coordinator(cycle)
    approval_hash = _approve(cycle, coordinator)
    coordinator.complete(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        adoption_approval_hash=approval_hash,
        capability=_spec(),
        observed_at="2026-08-20T00:00:00+00:00",
    )
    ledger = SqliteImprovementLedger(cycle.improvement_path)
    completion = ledger.latest_cycle_record(cycle.improvement_id, "adoption_completion")
    assert completion is not None

    rolled_back = _coordinator(cycle).rollback(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        adoption_completion_hash=completion.record_hash,
        capability=_spec(),
        reason="synthetic rollback 확인",
        evidence=("test:exact-cycle:rollback",),
    )
    repeated = _coordinator(cycle).rollback(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        adoption_completion_hash=completion.record_hash,
        capability=_spec(),
        reason="중복 호출",
        evidence=("test:exact-cycle:rollback-repeat",),
    )

    assert rolled_back.improvement_status == "rolled_back"
    assert repeated.improvement_status == "rolled_back"
    assert cycle.target.read_text(encoding="utf-8") == "return left - right\n"
    assert SqliteCapabilityRegistry(cycle.capability_path).get(
        "synthetic-calculator"
    ).status == "retired"
    assert cycle.worker.calls == 1


def test_restart_finishes_rollback_after_source_and_capability_are_restored(
    tmp_path: Path,
) -> None:
    cycle = _ready_cycle(tmp_path)
    coordinator = _coordinator(cycle)
    approval_hash = _approve(cycle, coordinator)
    coordinator.complete(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        adoption_approval_hash=approval_hash,
        capability=_spec(),
        observed_at="2026-08-20T00:00:00+00:00",
    )
    ledger = SqliteImprovementLedger(cycle.improvement_path)
    completion = ledger.latest_cycle_record(cycle.improvement_id, "adoption_completion")
    assert completion is not None
    proposals = SqliteChangeProposalRepository(cycle.change_path)
    ChangeAdoptionService(cycle.source, proposals).rollback("proposal-exact-cycle")
    registry = SqliteCapabilityRegistry(cycle.capability_path)
    registry.transition(_spec().id, "retired")
    assert ledger.get(cycle.improvement_id).status == "adopted"

    recovered = _coordinator(cycle).rollback(
        cycle.improvement_id,
        proposal_version=cycle.proposal_version,
        proposal_hash=cycle.proposal_hash,
        adoption_completion_hash=completion.record_hash,
        capability=_spec(),
        reason="중단된 rollback 완료",
        evidence=("test:exact-cycle:rollback-recovery",),
    )

    assert recovered.source_status == "rolled_back"
    assert recovered.capability_status == "retired"
    assert recovered.improvement_status == "rolled_back"
    assert cycle.target.read_text(encoding="utf-8") == "return left - right\n"


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
