"""Exact Shadow coordination from one adoption approval to an available capability."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

from companion.capabilities import Capability, SqliteCapabilityRegistry
from companion.change_adoption import ChangeAdoptionService
from companion.change_proposal import (
    SqliteChangeProposalRepository,
    StoredChangeProposal,
)
from companion.improvements import (
    ImprovementCycleRecord,
    ImprovementItem,
    ImprovementLedgerError,
    SqliteImprovementLedger,
)


class CapabilityAdoptionError(RuntimeError):
    """Raised when lifecycle, source, and capability state cannot be reconciled."""


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    name: str
    version: str
    supports: tuple[str, ...]
    limitations: tuple[str, ...]
    claim_scopes: tuple[str, ...]
    dependencies: tuple[str, ...]
    health_check: str

    def __post_init__(self) -> None:
        scalar = (self.id, self.name, self.version, self.health_check)
        collections = (self.supports, self.limitations, self.claim_scopes)
        if any(not item.strip() or item != item.strip() for item in scalar) or any(
            not values or any(not item.strip() for item in values)
            for values in collections
        ):
            raise CapabilityAdoptionError("capability specification is incomplete")
        if self.id in self.dependencies or any(
            not item.strip() for item in self.dependencies
        ):
            raise CapabilityAdoptionError("capability dependencies are invalid")
        for values in (*collections, self.dependencies):
            cleaned = tuple(dict.fromkeys(item.strip() for item in values))
            if values != cleaned:
                raise CapabilityAdoptionError(
                    "capability specification must be normalized and unique"
                )

    @property
    def spec_sha256(self) -> str:
        canonical = json.dumps(
            asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CapabilityAdoptionResult:
    improvement_id: str
    improvement_status: str
    change_proposal_id: str
    source_status: str
    capability_id: str
    capability_status: str
    message: str


class ExactCapabilityAdoption:
    """Use existing persistent stores as one fail-closed synthetic adoption journal."""

    def __init__(
        self,
        ledger: SqliteImprovementLedger,
        proposals: SqliteChangeProposalRepository,
        source_adoption: ChangeAdoptionService,
        capabilities: SqliteCapabilityRegistry,
    ) -> None:
        self._ledger = ledger
        self._proposals = proposals
        self._source_adoption = source_adoption
        self._capabilities = capabilities

    def approve(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
        validation_report_hash: str,
        capability: CapabilitySpec,
        decided_by: str,
        evidence: str,
    ) -> ImprovementItem:
        item = self._ledger.get(improvement_id)
        if item.status != "ready_to_adopt":
            raise CapabilityAdoptionError(
                f"adoption approval requires ready_to_adopt, got {item.status}"
            )
        stored, _ = self._binding(
            improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            capability=capability,
            expected_approval_hash=None,
            validation_report_hash=validation_report_hash,
        )
        baseline = stored.result.project_baseline
        if baseline is None:
            raise CapabilityAdoptionError(
                "change proposal has no privacy-safe project baseline"
            )
        if stored.status != "awaiting_adoption":
            raise CapabilityAdoptionError(
                "only an awaiting isolated proposal can receive adoption approval"
            )
        try:
            return self._ledger.approve_adoption(
                improvement_id,
                proposal_version=proposal_version,
                proposal_hash=proposal_hash,
                validation_report_hash=validation_report_hash,
                change_proposal_id=stored.proposal_id,
                patch_sha256=stored.patch_sha256,
                project_baseline_sha256=baseline.manifest_sha256,
                capability_spec_sha256=capability.spec_sha256,
                decided_by=decided_by,
                evidence=evidence,
            )
        except ImprovementLedgerError as error:
            raise CapabilityAdoptionError(str(error)) from error

    def complete(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
        adoption_approval_hash: str,
        capability: CapabilitySpec,
        observed_at: str,
    ) -> CapabilityAdoptionResult:
        item = self._ledger.get(improvement_id)
        if item.status == "adopted":
            return self._completed_result(
                item,
                adoption_approval_hash=adoption_approval_hash,
                capability=capability,
            )
        if item.status != "adoption_approved":
            raise CapabilityAdoptionError(
                f"adoption completion requires adoption_approved, got {item.status}"
            )
        stored, approval = self._binding(
            improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            capability=capability,
            expected_approval_hash=adoption_approval_hash,
            validation_report_hash=None,
        )
        existing = self._find_capability(capability.id)
        if existing is not None:
            self._require_capability_identity(
                existing,
                capability,
                improvement_id=improvement_id,
                proposal_version=proposal_version,
                proposal_hash=proposal_hash,
                stored=stored,
            )
        try:
            source_status = self._ensure_source_applied(stored)
            available = self._ensure_capability_available(
                capability,
                improvement_id=improvement_id,
                proposal_version=proposal_version,
                proposal_hash=proposal_hash,
                stored=stored,
                approval=approval,
                observed_at=observed_at,
            )
            completed = self._ledger.complete_adoption(
                improvement_id,
                proposal_version=proposal_version,
                proposal_hash=proposal_hash,
                adoption_approval_hash=approval.record_hash,
                source_reference=f"change_proposal:{stored.proposal_id}:applied",
                capability_reference=f"change_proposal:{stored.proposal_id}:capability:{available.id}",
            )
        except Exception as error:
            self._abort_if_safe(
                improvement_id,
                proposal_version=proposal_version,
                proposal_hash=proposal_hash,
                approval=approval,
                stored=stored,
                capability=capability,
                cause=error,
            )
            if isinstance(error, CapabilityAdoptionError):
                raise
            raise CapabilityAdoptionError(
                f"exact capability adoption failed: {type(error).__name__}"
            ) from error
        return CapabilityAdoptionResult(
            improvement_id=improvement_id,
            improvement_status=completed.status,
            change_proposal_id=stored.proposal_id,
            source_status=source_status,
            capability_id=available.id,
            capability_status=available.status,
            message="승인된 source와 capability를 확인한 뒤 겨울이의 채택을 완료했어.",
        )

    def rollback(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
        adoption_completion_hash: str,
        capability: CapabilitySpec,
        reason: str,
        evidence: tuple[str, ...],
    ) -> CapabilityAdoptionResult:
        item = self._ledger.get(improvement_id)
        completion = self._ledger.latest_cycle_record(
            improvement_id, "adoption_completion"
        )
        if completion is None or completion.record_hash != adoption_completion_hash:
            raise CapabilityAdoptionError(
                "rollback must target the exact adoption completion"
            )
        approval_hash = completion.payload.get("adoption_approval_hash")
        if not isinstance(approval_hash, str):
            raise CapabilityAdoptionError("adoption completion identity is invalid")
        stored, _ = self._binding(
            improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            capability=capability,
            expected_approval_hash=approval_hash,
            validation_report_hash=None,
        )
        registered = self._find_capability(capability.id)
        if registered is None:
            raise CapabilityAdoptionError("adopted capability is missing")
        self._require_capability_identity(
            registered,
            capability,
            improvement_id=improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            stored=stored,
        )
        if item.status == "rolled_back":
            if stored.status != "rolled_back" or registered.status != "retired":
                raise CapabilityAdoptionError("rolled back stores disagree")
            return self._result(item, stored, registered, "이미 rollback이 완료돼 있어.")
        if item.status != "adopted":
            raise CapabilityAdoptionError(
                f"rollback requires adopted, got {item.status}"
            )
        source_status = self._ensure_source_rolled_back(stored)
        registered = self._retire_capability(registered)
        rolled_back = self._ledger.rollback_adoption(
            improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            reason=reason,
            evidence=evidence,
        )
        return CapabilityAdoptionResult(
            improvement_id=improvement_id,
            improvement_status=rolled_back.status,
            change_proposal_id=stored.proposal_id,
            source_status=source_status,
            capability_id=registered.id,
            capability_status=registered.status,
            message="source를 복원하고 capability를 retired로 바꾼 뒤 rollback을 기록했어.",
        )

    def _binding(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
        capability: CapabilitySpec,
        expected_approval_hash: str | None,
        validation_report_hash: str | None,
    ) -> tuple[StoredChangeProposal, ImprovementCycleRecord]:
        item = self._ledger.get(improvement_id)
        current = item.current_proposal
        if (
            current is None
            or current.version != proposal_version
            or current.proposal_hash != proposal_hash
        ):
            raise CapabilityAdoptionError(
                "adoption must target the current Improvement proposal"
            )
        worker = self._ledger.latest_cycle_record(improvement_id, "worker_result")
        report = self._ledger.latest_cycle_record(improvement_id, "validation_report")
        if worker is None or report is None or report.payload.get("passed") is not True:
            raise CapabilityAdoptionError(
                "adoption requires a passing persisted validation report"
            )
        if report.payload.get("worker_result_hash") != worker.record_hash:
            raise CapabilityAdoptionError("validation and Worker identities differ")
        if validation_report_hash is not None and report.record_hash != validation_report_hash:
            raise CapabilityAdoptionError("validation report hash differs")
        artifact_id = worker.payload.get("artifact_id")
        artifact_hash = worker.payload.get("artifact_hash")
        if not isinstance(artifact_id, str) or not isinstance(artifact_hash, str):
            raise CapabilityAdoptionError("Worker artifact identity is invalid")
        stored = self._proposals.get(artifact_id)
        if stored is None or stored.patch_sha256 != artifact_hash:
            raise CapabilityAdoptionError("isolated proposal and Worker artifact differ")
        baseline = stored.result.project_baseline
        if baseline is None:
            raise CapabilityAdoptionError("isolated proposal baseline is missing")

        if expected_approval_hash is None:
            return stored, report
        approval = self._ledger.latest_cycle_record(
            improvement_id, "adoption_approval"
        )
        if approval is None or approval.record_hash != expected_approval_hash:
            raise CapabilityAdoptionError("adoption approval hash differs")
        expected = {
            "validation_report_hash": report.record_hash,
            "worker_result_hash": worker.record_hash,
            "change_proposal_id": stored.proposal_id,
            "patch_sha256": stored.patch_sha256,
            "project_baseline_sha256": baseline.manifest_sha256,
            "capability_spec_sha256": capability.spec_sha256,
        }
        if any(approval.payload.get(key) != value for key, value in expected.items()):
            raise CapabilityAdoptionError("adoption approval binding differs")
        return stored, approval

    def _ensure_source_applied(self, stored: StoredChangeProposal) -> str:
        current = self._proposals.get(stored.proposal_id)
        if current is None:
            raise CapabilityAdoptionError("linked change proposal is missing")
        if current.status == "awaiting_adoption":
            current = self._proposals.record_decision(
                current.proposal_id,
                decision="adopt",
                expected_patch_sha256=current.patch_sha256,
            )
        if current.status == "adoption_approved":
            return self._source_adoption.apply(
                current.proposal_id,
                expected_patch_sha256=current.patch_sha256,
            ).status
        if current.status == "applying":
            return self._source_adoption.recover(current.proposal_id).status
        if current.status == "applied":
            return current.status
        raise CapabilityAdoptionError(
            f"change proposal cannot be applied from {current.status}"
        )

    def _ensure_source_rolled_back(self, stored: StoredChangeProposal) -> str:
        current = self._proposals.get(stored.proposal_id)
        if current is None:
            raise CapabilityAdoptionError("linked change proposal is missing")
        if current.status == "applied":
            return self._source_adoption.rollback(current.proposal_id).status
        if current.status == "rolling_back":
            return self._source_adoption.recover(current.proposal_id).status
        if current.status == "rolled_back":
            return current.status
        raise CapabilityAdoptionError(
            f"change proposal cannot be rolled back from {current.status}"
        )

    def _ensure_capability_available(
        self,
        spec: CapabilitySpec,
        *,
        improvement_id: str,
        proposal_version: int,
        proposal_hash: str,
        stored: StoredChangeProposal,
        approval: ImprovementCycleRecord,
        observed_at: str,
    ) -> Capability:
        capability = self._find_capability(spec.id)
        if capability is None:
            capability = self._capabilities.propose(
                capability_id=spec.id,
                name=spec.name,
                version=spec.version,
                supports=spec.supports,
                limitations=spec.limitations,
                claim_scopes=spec.claim_scopes,
                dependencies=spec.dependencies,
                health_check=spec.health_check,
                adopted_change=_adopted_change(
                    improvement_id, proposal_version, proposal_hash
                ),
                rollback_target=_rollback_target(stored),
            )
        self._require_capability_identity(
            capability,
            spec,
            improvement_id=improvement_id,
            proposal_version=proposal_version,
            proposal_hash=proposal_hash,
            stored=stored,
        )
        for status in ("approved", "building", "testing"):
            if capability.status == status:
                continue
            previous = {"approved": "proposed", "building": "approved", "testing": "building"}
            if capability.status == previous[status]:
                capability = self._capabilities.transition(spec.id, status)
        if capability.status == "testing":
            references = (
                ("adoption", f"change_proposal:{stored.proposal_id}:{approval.record_hash}"),
                ("health", f"change_proposal:{stored.proposal_id}:repository-verifier"),
                ("test", f"verification:{approval.payload['validation_report_hash']}"),
            )
            for kind, reference in references:
                if not any(
                    item.kind == kind and item.reference == reference and item.passed
                    for item in capability.evidence
                ):
                    self._capabilities.record_evidence(
                        spec.id,
                        kind=kind,
                        reference=reference,
                        passed=True,
                        observed_at=observed_at,
                    )
            capability = self._capabilities.transition(spec.id, "available")
        if capability.status != "available":
            raise CapabilityAdoptionError(
                f"capability did not reach available from {capability.status}"
            )
        return capability

    def _require_capability_identity(
        self,
        capability: Capability,
        spec: CapabilitySpec,
        *,
        improvement_id: str,
        proposal_version: int,
        proposal_hash: str,
        stored: StoredChangeProposal,
    ) -> None:
        actual = (
            capability.id,
            capability.name,
            capability.version,
            capability.supports,
            capability.limitations,
            capability.claim_scopes,
            capability.dependencies,
            capability.health_check,
            capability.adopted_change,
            capability.rollback_target,
        )
        expected = (
            spec.id,
            spec.name,
            spec.version,
            spec.supports,
            spec.limitations,
            spec.claim_scopes,
            spec.dependencies,
            spec.health_check,
            _adopted_change(improvement_id, proposal_version, proposal_hash),
            _rollback_target(stored),
        )
        if actual != expected:
            raise CapabilityAdoptionError("existing capability identity differs")

    def _abort_if_safe(
        self,
        improvement_id: str,
        *,
        proposal_version: int,
        proposal_hash: str,
        approval: ImprovementCycleRecord,
        stored: StoredChangeProposal,
        capability: CapabilitySpec,
        cause: Exception,
    ) -> None:
        try:
            current = self._proposals.get(stored.proposal_id)
            if current is None:
                return
            if current.status == "applying":
                self._source_adoption.recover(current.proposal_id)
                current = self._proposals.get(current.proposal_id)
            if current is not None and current.status == "applied":
                self._source_adoption.rollback(current.proposal_id)
                current = self._proposals.get(current.proposal_id)
            if current is None or current.status not in {
                "awaiting_adoption",
                "adoption_approved",
                "stale",
                "apply_failed",
                "rolled_back",
            }:
                return
            registered = self._find_capability(capability.id)
            if registered is not None and registered.status != "retired":
                try:
                    self._require_capability_identity(
                        registered,
                        capability,
                        improvement_id=improvement_id,
                        proposal_version=proposal_version,
                        proposal_hash=proposal_hash,
                        stored=stored,
                    )
                except CapabilityAdoptionError:
                    pass
                else:
                    self._retire_capability(registered)
            item = self._ledger.get(improvement_id)
            if item.status == "adoption_approved":
                failure_hash = hashlib.sha256(repr(cause).encode("utf-8")).hexdigest()
                self._ledger.fail_adoption(
                    improvement_id,
                    proposal_version=proposal_version,
                    proposal_hash=proposal_hash,
                    adoption_approval_hash=approval.record_hash,
                    reason=f"{type(cause).__name__}:{failure_hash[:16]}",
                    evidence=(f"change_proposal:{stored.proposal_id}:adoption-failed",),
                )
        except Exception:
            return

    def _retire_capability(self, capability: Capability) -> Capability:
        if capability.status == "retired":
            return capability
        return self._capabilities.transition(capability.id, "retired")

    def _find_capability(self, capability_id: str) -> Capability | None:
        return next(
            (item for item in self._capabilities.list() if item.id == capability_id),
            None,
        )

    def _completed_result(
        self,
        item: ImprovementItem,
        *,
        adoption_approval_hash: str,
        capability: CapabilitySpec,
    ) -> CapabilityAdoptionResult:
        completion = self._ledger.latest_cycle_record(item.id, "adoption_completion")
        if completion is None or completion.payload.get(
            "adoption_approval_hash"
        ) != adoption_approval_hash:
            raise CapabilityAdoptionError("completed adoption identity differs")
        approval = self._ledger.latest_cycle_record(item.id, "adoption_approval")
        if approval is None or approval.record_hash != adoption_approval_hash:
            raise CapabilityAdoptionError("completed adoption approval is missing")
        if approval.payload.get("capability_spec_sha256") != capability.spec_sha256:
            raise CapabilityAdoptionError("completed capability binding differs")
        stored_id = approval.payload.get("change_proposal_id")
        if not isinstance(stored_id, str):
            raise CapabilityAdoptionError("completed change proposal identity is invalid")
        stored = self._proposals.get(stored_id)
        registered = self._find_capability(capability.id)
        if stored is None or registered is None:
            raise CapabilityAdoptionError("completed adoption stores are missing")
        if stored.status != "applied" or registered.status != "available":
            raise CapabilityAdoptionError("completed adoption stores disagree")
        current = item.current_proposal
        if current is None:
            raise CapabilityAdoptionError("completed Improvement proposal is missing")
        self._require_capability_identity(
            registered,
            capability,
            improvement_id=item.id,
            proposal_version=current.version,
            proposal_hash=current.proposal_hash,
            stored=stored,
        )
        return self._result(item, stored, registered, "이미 exact adoption이 완료돼 있어.")

    @staticmethod
    def _result(
        item: ImprovementItem,
        stored: StoredChangeProposal,
        capability: Capability,
        message: str,
    ) -> CapabilityAdoptionResult:
        return CapabilityAdoptionResult(
            improvement_id=item.id,
            improvement_status=item.status,
            change_proposal_id=stored.proposal_id,
            source_status=stored.status,
            capability_id=capability.id,
            capability_status=capability.status,
            message=message,
        )


def _adopted_change(
    improvement_id: str, proposal_version: int, proposal_hash: str
) -> str:
    return f"improvement:{improvement_id}:v{proposal_version}:{proposal_hash}"


def _rollback_target(stored: StoredChangeProposal) -> str:
    return f"change_proposal:{stored.proposal_id}:{stored.patch_sha256}"
