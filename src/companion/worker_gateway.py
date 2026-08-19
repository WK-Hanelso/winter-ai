"""Provider-independent contracts for bounded Worker project tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Protocol, cast

from companion.work_orchestration import EvidenceReport


class GatewayDeniedError(RuntimeError):
    """A request crossed the fixed read-only project boundary."""


@dataclass(frozen=True)
class ProjectToolCall:
    call_id: str
    operation: str
    path: str | None = None
    query: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        if not self.call_id.strip():
            raise ValueError("tool call id must not be empty")
        if not self.operation.strip():
            raise ValueError("tool operation must not be empty")


@dataclass(frozen=True)
class GatewayEvidence:
    evidence_id: str
    locator: str
    content_sha256: str


@dataclass(frozen=True)
class GatewayToolResult:
    call_id: str
    operation: str
    content: str
    evidence: tuple[GatewayEvidence, ...]


@dataclass(frozen=True)
class GatewayAuditEntry:
    call_id: str
    operation: str
    decision: str
    reason: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class GatewayWorkerResult:
    report: EvidenceReport
    audit_log: tuple[GatewayAuditEntry, ...]
    evidence_catalog: tuple[GatewayEvidence, ...]
    worker: str
    model: str


class _GatewayState(Protocol):
    @property
    def audit_log(self) -> tuple[GatewayAuditEntry, ...]: ...

    @property
    def evidence_catalog(self) -> tuple[GatewayEvidence, ...]: ...


def serialize_gateway_state(gateway: _GatewayState) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "audit_log": [asdict(item) for item in gateway.audit_log],
            "evidence_catalog": [
                asdict(item) for item in gateway.evidence_catalog
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def parse_gateway_state(
    payload: object,
) -> tuple[tuple[GatewayAuditEntry, ...], tuple[GatewayEvidence, ...]]:
    data = _mapping(payload, "gateway state")
    if set(data) != {"schema_version", "audit_log", "evidence_catalog"}:
        raise GatewayDeniedError("gateway state has unexpected fields")
    if data["schema_version"] != 1:
        raise GatewayDeniedError("unsupported gateway state version")
    audit = tuple(
        _audit_entry(item, index)
        for index, item in enumerate(_sequence(data["audit_log"], "audit_log"))
    )
    evidence = tuple(
        _evidence(item, index)
        for index, item in enumerate(
            _sequence(data["evidence_catalog"], "evidence_catalog")
        )
    )
    ids = tuple(item.evidence_id for item in evidence)
    if len(ids) != len(set(ids)):
        raise GatewayDeniedError("gateway state contains duplicate evidence ids")
    call_ids = tuple(item.call_id for item in audit)
    if len(call_ids) != len(set(call_ids)):
        raise GatewayDeniedError("gateway state contains duplicate call ids")
    known = frozenset(ids)
    for entry in audit:
        if set(entry.evidence_ids) - known:
            raise GatewayDeniedError("gateway audit references unknown evidence ids")
        if entry.decision == "denied" and entry.evidence_ids:
            raise GatewayDeniedError("denied gateway audit cannot issue evidence")
    return audit, evidence


def validate_report_evidence(
    report: EvidenceReport,
    catalog: tuple[GatewayEvidence, ...],
) -> None:
    """Reject evidence that was not issued by this gateway session."""

    known = {item.evidence_id: item.locator for item in catalog}
    for item in report.evidence:
        if known.get(item.evidence_id) != item.locator:
            raise GatewayDeniedError(
                "worker report contains evidence outside the gateway session"
            )


def _audit_entry(value: object, index: int) -> GatewayAuditEntry:
    name = f"audit_log[{index}]"
    data = _mapping(value, name)
    expected = {"call_id", "operation", "decision", "reason", "evidence_ids"}
    if set(data) != expected:
        raise GatewayDeniedError(f"{name} has unexpected fields")
    decision = _string(data["decision"], f"{name}.decision")
    if decision not in {"allowed", "denied"}:
        raise GatewayDeniedError(f"{name} has unsupported decision")
    return GatewayAuditEntry(
        _string(data["call_id"], f"{name}.call_id"),
        _string(data["operation"], f"{name}.operation"),
        decision,
        _string(data["reason"], f"{name}.reason"),
        _strings(data["evidence_ids"], f"{name}.evidence_ids"),
    )


def _evidence(value: object, index: int) -> GatewayEvidence:
    name = f"evidence_catalog[{index}]"
    data = _mapping(value, name)
    if set(data) != {"evidence_id", "locator", "content_sha256"}:
        raise GatewayDeniedError(f"{name} has unexpected fields")
    digest = _string(data["content_sha256"], f"{name}.content_sha256")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise GatewayDeniedError(f"{name} has invalid content hash")
    return GatewayEvidence(
        _string(data["evidence_id"], f"{name}.evidence_id"),
        _string(data["locator"], f"{name}.locator"),
        digest,
    )


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GatewayDeniedError(f"{name} must be an object")
    return cast(dict[str, Any], value)


def _sequence(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise GatewayDeniedError(f"{name} must be an array")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GatewayDeniedError(f"{name} must be a non-empty string")
    return value.strip()


def _strings(value: object, name: str) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{name}[{index}]")
        for index, item in enumerate(_sequence(value, name))
    )
