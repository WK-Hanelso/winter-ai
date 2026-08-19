"""Deterministic Shadow lifecycle proving Worker, verification, and adoption gates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol, cast

from companion.improvements import (
    ImprovementItem,
    ImprovementLedgerError,
    ImprovementProposal,
    SqliteImprovementLedger,
)


@dataclass(frozen=True)
class WorkerResult:
    succeeded: bool
    summary: str
    evidence: tuple[str, ...]
    artifact_id: str
    artifact_hash: str


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    summary: str
    checks: tuple[str, ...]
    evidence: tuple[str, ...]


class ImprovementWorker(Protocol):
    name: str

    def execute(self, proposal: ImprovementProposal) -> WorkerResult: ...


class ImprovementVerifier(Protocol):
    def verify(
        self, proposal: ImprovementProposal, result: WorkerResult
    ) -> ValidationReport: ...


class FakeImprovementWorker:
    """Configurable fake whose call count makes authorization failures observable."""

    name = "deterministic-fake-worker"

    def __init__(self, result: WorkerResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, proposal: ImprovementProposal) -> WorkerResult:
        del proposal
        self.calls += 1
        return self.result


class FakeIndependentVerifier:
    """Separate fake verifier; it never trusts the Worker's success flag implicitly."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        self.calls = 0

    def verify(
        self, proposal: ImprovementProposal, result: WorkerResult
    ) -> ValidationReport:
        del proposal, result
        self.calls += 1
        return self.report


class FakeWorkerLifecycle:
    """Drive one proposal through explicit, persistent Level 3 Shadow gates."""

    def __init__(
        self,
        ledger: SqliteImprovementLedger,
        worker: ImprovementWorker,
        verifier: ImprovementVerifier,
    ) -> None:
        self._ledger = ledger
        self._worker = worker
        self._verifier = verifier

    def run_worker(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
    ) -> ImprovementItem:
        started = self._ledger.begin_implementation(
            improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
        )
        proposal = _current_proposal(started, proposal_version, proposal_hash)
        try:
            result = self._worker.execute(proposal)
        except Exception as error:
            failure_hash = hashlib.sha256(repr(error).encode("utf-8")).hexdigest()
            return self._ledger.record_worker_result(
                improvement_id,
                proposal_version=proposal_version,
                proposal_hash=proposal_hash,
                worker_name=self._worker.name,
                succeeded=False,
                summary=f"Worker exception: {type(error).__name__}",
                evidence=("test:fake-worker-exception",),
                artifact_id="fake-worker-exception",
                artifact_hash=failure_hash,
            )
        return self._ledger.record_worker_result(
            improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            worker_name=self._worker.name,
            succeeded=result.succeeded,
            summary=result.summary,
            evidence=result.evidence,
            artifact_id=result.artifact_id,
            artifact_hash=result.artifact_hash,
        )

    def validate(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
    ) -> ImprovementItem:
        item = self._ledger.get(improvement_id)
        proposal = _current_proposal(item, proposal_version, proposal_hash)
        if item.status != "validating":
            raise ImprovementLedgerError(
                f"validation requires status validating, got {item.status}"
            )
        record = self._ledger.latest_cycle_record(improvement_id, "worker_result")
        if record is None:
            raise ImprovementLedgerError("validation requires a Worker result")
        payload = record.payload
        result = WorkerResult(
            succeeded=cast(bool, payload["succeeded"]),
            summary=cast(str, payload["summary"]),
            evidence=tuple(cast(list[str], payload["evidence"])),
            artifact_id=cast(str, payload["artifact_id"]),
            artifact_hash=cast(str, payload["artifact_hash"]),
        )
        report = self._verifier.verify(proposal, result)
        return self._ledger.record_validation(
            improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            worker_result_hash=record.record_hash,
            passed=report.passed,
            summary=report.summary,
            checks=report.checks,
            evidence=report.evidence,
        )


def _current_proposal(
    item: ImprovementItem, proposal_version: int, proposal_hash: str
) -> ImprovementProposal:
    proposal = item.current_proposal
    if proposal is None or proposal.version != proposal_version:
        raise ImprovementLedgerError("cycle must target the current proposal version")
    if proposal.proposal_hash != proposal_hash:
        raise ImprovementLedgerError("cycle proposal hash does not match")
    return proposal
