"""Shadow-only bridge from an approved Improvement to isolated implementation."""

from __future__ import annotations

import hashlib

from companion.change_proposal import (
    IsolatedWorkExecutor,
    SqliteChangeProposalRepository,
    StoredChangeProposal,
)
from companion.improvements import (
    ImprovementItem,
    ImprovementProposal,
    SqliteImprovementLedger,
)
from companion.isolated_work import ImplementationAuthorization, IsolatedWorkResult


class IsolatedLifecycleBridgeError(RuntimeError):
    """Raised when two Shadow stores cannot be linked without guessing."""


class IsolatedLifecycleBridge:
    """Bind exact proposal identities without applying their patches."""

    def __init__(
        self,
        ledger: SqliteImprovementLedger,
        proposals: SqliteChangeProposalRepository,
        worker: IsolatedWorkExecutor,
    ) -> None:
        self._ledger = ledger
        self._proposals = proposals
        self._worker = worker

    def execute(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
    ) -> ImprovementItem:
        item = self._ledger.get(improvement_id)
        proposal = _current_proposal(item, proposal_version, proposal_hash)
        authorization = build_implementation_authorization(proposal)
        may_execute = item.status == "approved"
        if may_execute:
            self._ledger.begin_implementation(
                improvement_id,
                proposal_version=proposal_version,
                proposal_hash=proposal_hash,
            )
        elif item.status != "implementing":
            raise IsolatedLifecycleBridgeError(
                f"isolated execution requires approved or implementing, got {item.status}"
            )

        stored = self._proposals.get_by_authorization_id(
            authorization.authorization_id
        )
        if stored is None and not may_execute:
            raise IsolatedLifecycleBridgeError(
                "implementation started without a persisted isolated proposal; "
                "manual recovery is required and the Worker was not called again"
            )
        if stored is None:
            try:
                result = self._worker.execute(authorization)
                _validate_result(authorization, result)
                stored = self._proposals.record(authorization, result)
            except Exception as error:
                failure_hash = hashlib.sha256(repr(error).encode("utf-8")).hexdigest()
                return self._ledger.record_worker_result(
                    improvement_id,
                    proposal_version=proposal_version,
                    proposal_hash=proposal_hash,
                    worker_name="isolated-implementation-shadow",
                    succeeded=False,
                    summary=f"isolated Worker failed: {type(error).__name__}",
                    evidence=(
                        f"worker_run:{authorization.authorization_id}:failure:{failure_hash[:16]}",
                    ),
                    artifact_id=authorization.authorization_id,
                    artifact_hash=failure_hash,
                )
        _validate_link(authorization, stored)
        return self._ledger.record_worker_result(
            improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            worker_name=stored.result.worker,
            succeeded=True,
            summary=f"isolated proposal persisted: {stored.proposal_id}",
            evidence=(f"change_proposal:{stored.proposal_id}",),
            artifact_id=stored.proposal_id,
            artifact_hash=stored.patch_sha256,
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
            raise IsolatedLifecycleBridgeError(
                f"isolated validation requires validating, got {item.status}"
            )
        authorization = build_implementation_authorization(proposal)
        worker_record = self._ledger.latest_cycle_record(
            improvement_id, "worker_result"
        )
        if worker_record is None:
            raise IsolatedLifecycleBridgeError("persisted Worker result is missing")
        artifact_id = worker_record.payload.get("artifact_id")
        artifact_hash = worker_record.payload.get("artifact_hash")
        if not isinstance(artifact_id, str) or not isinstance(artifact_hash, str):
            raise IsolatedLifecycleBridgeError("Worker artifact identity is invalid")
        stored = self._proposals.get(artifact_id)
        if stored is None:
            raise IsolatedLifecycleBridgeError("linked isolated proposal is missing")
        _validate_link(authorization, stored)
        if stored.patch_sha256 != artifact_hash:
            raise IsolatedLifecycleBridgeError(
                "lifecycle artifact hash differs from the isolated patch"
            )

        required = {authorization.verification_profile, "python_structure"}
        names = {check.name for check in stored.result.verifications}
        passed = (
            stored.result.status == "awaiting_adoption"
            and required.issubset(names)
            and all(
                check.status == "pass" and check.exit_code == 0
                for check in stored.result.verifications
            )
        )
        checks = tuple(
            f"{check.name}:{check.status}:exit={check.exit_code}"
            for check in stored.result.verifications
        )
        evidence = tuple(
            _verification_reference(stored.proposal_id, position, check)
            for position, check in enumerate(stored.result.verifications)
        )
        return self._ledger.record_validation(
            improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            worker_result_hash=worker_record.record_hash,
            passed=passed,
            summary=(
                "required host verification passed"
                if passed
                else "required host verification failed or is incomplete"
            ),
            checks=checks,
            evidence=evidence,
        )


def build_implementation_authorization(
    proposal: ImprovementProposal,
) -> ImplementationAuthorization:
    acceptance = "\n".join(f"- {item}" for item in proposal.acceptance_criteria)
    return ImplementationAuthorization(
        authorization_id=(
            f"improvement-{proposal.improvement_id}-v{proposal.version}-"
            f"{proposal.proposal_hash[:16]}"
        ),
        status="approved",
        objective=f"{proposal.solution}\nAcceptance criteria:\n{acceptance}",
        allowed_paths=proposal.implementation_scope,
        verification_profile="python_unittest",
    )


def _current_proposal(
    item: ImprovementItem, proposal_version: int, proposal_hash: str
) -> ImprovementProposal:
    proposal = item.current_proposal
    if proposal is None or proposal.version != proposal_version:
        raise IsolatedLifecycleBridgeError(
            "bridge must target the current proposal version"
        )
    if proposal.proposal_hash != proposal_hash:
        raise IsolatedLifecycleBridgeError("bridge proposal hash does not match")
    return proposal


def _validate_result(
    authorization: ImplementationAuthorization, result: IsolatedWorkResult
) -> None:
    if result.authorization_id != authorization.authorization_id:
        raise IsolatedLifecycleBridgeError(
            "isolated result belongs to a different authorization"
        )
    changed = tuple(item.path for item in result.changed_files)
    if len(changed) != len(set(changed)) or not set(changed).issubset(
        authorization.allowed_paths
    ):
        raise IsolatedLifecycleBridgeError(
            "isolated result changed paths outside the approved scope"
        )


def _validate_link(
    authorization: ImplementationAuthorization, stored: StoredChangeProposal
) -> None:
    _validate_result(authorization, stored.result)
    if stored.authorization_id != authorization.authorization_id:
        raise IsolatedLifecycleBridgeError(
            "stored proposal belongs to a different authorization"
        )
    if stored.objective != authorization.objective:
        raise IsolatedLifecycleBridgeError(
            "stored proposal objective differs from the approved proposal"
        )


def _verification_reference(
    proposal_id: str, position: int, check: object
) -> str:
    digest = hashlib.sha256(repr(check).encode("utf-8")).hexdigest()[:16]
    return f"verification:{proposal_id}:{position}:{digest}"
