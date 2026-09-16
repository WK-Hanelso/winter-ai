"""Persistent, evidence-gated facts about what Winter can actually do."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Protocol
from uuid import uuid4

from configs.dialogue.self_model import CAPABILITIES

from companion.identity import CompanionIdentity

OPERATION_DESCRIPTION = "\n".join((
    "동작 방식(사실만 말해):",
    "- 천우가 한 말을 대화가 끝난 뒤 사실 문장으로 정리해 기억하고, "
    "다음 대화에서 그걸 배경으로 쓴다.",
    "- 필요하면 로컬 검색 서버로 웹을 검색해 출처와 함께 답한다. 검색어는 화면에 표시된다.",
    '- 코드 확인은 천우가 시키거나 네가 정말 코드를 봐야 답할 수 있다고 판단할 때, '
    '코덱스에 읽기 작업을 제안할 수 있다. 천우가 터미널에서 승인하면 Codex가 '
    '고정 읽기 범위(README.md, pyproject.toml, src/, tests/)만 읽고 답을 돌려준다. '
    "'코덱스 쓸 수 있어?'에는 '시키거나 필요하면 제안해서 쓸 수 있다'고 답해.",
    "- 날짜·시간이 있는 약속은 일정으로 저장할 수 있고, "
    "저장 결과는 실제 기록으로만 말한다.",
    "- 모르는 동작 원리는 지어내지 말고 모른다고 말해.",
))

CAPABILITY_STATUSES = frozenset(
    {
        "proposed",
        "approved",
        "building",
        "testing",
        "available",
        "failed",
        "degraded",
        "retired",
    }
)
EVIDENCE_KINDS = frozenset({"health", "test", "evaluation", "adoption"})
_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"approved", "retired"}),
    "approved": frozenset({"building", "retired"}),
    "building": frozenset({"testing", "failed", "retired"}),
    "testing": frozenset({"available", "failed", "retired"}),
    "available": frozenset({"degraded", "retired"}),
    "degraded": frozenset({"available", "failed", "retired"}),
    "failed": frozenset({"building", "retired"}),
    "retired": frozenset(),
}


class CapabilityRegistryError(RuntimeError):
    """Raised when persistent capability state violates its contract."""


@dataclass(frozen=True)
class CapabilityEvidence:
    id: str
    capability_id: str
    kind: str
    reference: str
    passed: bool
    observed_at: str
    created_at: str


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    version: str
    status: str
    supports: tuple[str, ...]
    limitations: tuple[str, ...]
    claim_scopes: tuple[str, ...]
    dependencies: tuple[str, ...]
    health_check: str
    adopted_change: str | None
    rollback_target: str | None
    created_at: str
    updated_at: str
    evidence: tuple[CapabilityEvidence, ...]


@dataclass(frozen=True)
class SelfCapability:
    id: str
    name: str
    version: str
    status: str
    supports: tuple[str, ...]
    limitations: tuple[str, ...]
    claim_scopes: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class SelfModelSnapshot:
    identity_name: str
    identity_version: str
    available: tuple[SelfCapability, ...]
    unavailable: tuple[SelfCapability, ...]

    @property
    def authorized_claim_scopes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                scope
                for capability in self.available
                for scope in capability.claim_scopes
            )
        )

    def system_message(self, *, companion: bool = False) -> str:
        if companion:
            return "\n".join((
                "[겨울이가 할 수 있는 것 — 사실만 말해]",
                *(f"{index}. {name}: {description}"
                  for index, (name, description) in enumerate(CAPABILITIES, 1)),
                "여기 없는 기능은 할 수 있다고 말하지 마.",
            ))
        lines = [
            "[검증된 SelfModel — 읽기 전용]",
            f"정체성: {self.identity_name} {self.identity_version}",
            "아래 available 기능만 지금 할 수 있다고 말해.",
        ]
        if self.available:
            for item in self.available:
                lines.append(
                    f"- available {item.name} {item.version}: "
                    + "; ".join(item.supports)
                )
                if item.limitations:
                    lines.append("  제한: " + "; ".join(item.limitations))
        else:
            lines.append("- 검증된 available 기능 없음")
        if self.unavailable:
            lines.append("현재 available이 아닌 기능:")
            for item in self.unavailable:
                detail = "; ".join(item.limitations) or "검증 근거가 부족함"
                lines.append(f"- {item.name} ({item.status}): {detail}")
        lines.append("목록에 없거나 available이 아닌 기능은 가능하다고 추측하지 마.")
        lines.append(OPERATION_DESCRIPTION)
        return "\n".join(lines)


class SelfModelProvider(Protocol):
    def snapshot(self) -> SelfModelSnapshot: ...


class SqliteCapabilityRegistry:
    """Store capability definitions, evidence and lifecycle transitions."""

    _SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def propose(
        self,
        *,
        capability_id: str,
        name: str,
        version: str,
        supports: tuple[str, ...],
        limitations: tuple[str, ...],
        claim_scopes: tuple[str, ...],
        dependencies: tuple[str, ...] = (),
        health_check: str,
        adopted_change: str | None = None,
        rollback_target: str | None = None,
    ) -> Capability:
        fields = {
            "capability id": capability_id,
            "capability name": name,
            "capability version": version,
            "health check": health_check,
        }
        if any(not value.strip() for value in fields.values()):
            raise CapabilityRegistryError("capability identity and health check are required")
        supports = _nonempty_items(supports, "supports")
        limitations = _nonempty_items(limitations, "limitations")
        claim_scopes = _nonempty_items(claim_scopes, "claim scopes")
        dependencies = tuple(dict.fromkeys(_clean_items(dependencies)))
        if capability_id in dependencies:
            raise CapabilityRegistryError("capability cannot depend on itself")
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO capabilities
                    (id, name, version, status, supports_json, limitations_json,
                     claim_scopes_json, dependencies_json, health_check,
                     adopted_change, rollback_target, created_at, updated_at)
                    VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        capability_id.strip(),
                        name.strip(),
                        version.strip(),
                        _json(supports),
                        _json(limitations),
                        _json(claim_scopes),
                        _json(dependencies),
                        health_check.strip(),
                        _optional(adopted_change),
                        _optional(rollback_target),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise CapabilityRegistryError(
                f"capability already exists: {capability_id.strip()}"
            ) from error
        except sqlite3.Error as error:
            raise CapabilityRegistryError(f"could not propose capability: {error}") from error
        return self.get(capability_id)

    def record_evidence(
        self,
        capability_id: str,
        *,
        kind: str,
        reference: str,
        passed: bool,
        observed_at: str,
    ) -> CapabilityEvidence:
        self.get(capability_id)
        if kind not in EVIDENCE_KINDS:
            raise CapabilityRegistryError(f"unsupported capability evidence kind: {kind}")
        if not reference.strip() or not observed_at.strip():
            raise CapabilityRegistryError("evidence reference and observed_at are required")
        evidence = CapabilityEvidence(
            id=str(uuid4()),
            capability_id=capability_id,
            kind=kind,
            reference=reference.strip(),
            passed=passed,
            observed_at=observed_at.strip(),
            created_at=_now(),
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO capability_evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        evidence.id,
                        evidence.capability_id,
                        evidence.kind,
                        evidence.reference,
                        int(evidence.passed),
                        evidence.observed_at,
                        evidence.created_at,
                    ),
                )
        except sqlite3.Error as error:
            raise CapabilityRegistryError(
                f"could not record capability evidence: {error}"
            ) from error
        return evidence

    def transition(self, capability_id: str, status: str) -> Capability:
        current = self.get(capability_id)
        if status not in CAPABILITY_STATUSES:
            raise CapabilityRegistryError(f"unsupported capability status: {status}")
        if status not in _TRANSITIONS[current.status]:
            raise CapabilityRegistryError(
                f"cannot transition capability {capability_id} "
                f"from {current.status} to {status}"
            )
        if status == "available":
            self._validate_available(current)
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE capabilities SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, capability_id),
                )
                connection.execute(
                    "INSERT INTO capability_transitions VALUES (?, ?, ?, ?, ?)",
                    (str(uuid4()), capability_id, current.status, status, now),
                )
        except sqlite3.Error as error:
            raise CapabilityRegistryError(
                f"could not transition capability: {error}"
            ) from error
        return self.get(capability_id)

    def get(self, capability_id: str) -> Capability:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM capabilities WHERE id = ?", (capability_id,)
                ).fetchone()
                evidence_rows = connection.execute(
                    """
                    SELECT * FROM capability_evidence
                    WHERE capability_id = ?
                    ORDER BY observed_at, created_at, id
                    """,
                    (capability_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise CapabilityRegistryError(f"could not read capability: {error}") from error
        if row is None:
            raise CapabilityRegistryError(f"capability not found: {capability_id}")
        return _capability(row, evidence_rows)

    def list(self) -> tuple[Capability, ...]:
        try:
            with self._connect() as connection:
                ids = [
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM capabilities ORDER BY id"
                    ).fetchall()
                ]
        except sqlite3.Error as error:
            raise CapabilityRegistryError(f"could not list capabilities: {error}") from error
        return tuple(self.get(capability_id) for capability_id in ids)

    def _validate_available(self, capability: Capability) -> None:
        latest_health = next(
            (
                item
                for item in reversed(capability.evidence)
                if item.kind == "health"
            ),
            None,
        )
        if latest_health is None or not latest_health.passed:
            raise CapabilityRegistryError(
                "available capability requires latest passing health evidence"
            )
        if not any(
            item.passed and item.kind in {"test", "evaluation"}
            for item in capability.evidence
        ):
            raise CapabilityRegistryError(
                "available capability requires passing test or evaluation evidence"
            )
        for dependency_id in capability.dependencies:
            dependency = self.get(dependency_id)
            if dependency.status != "available":
                raise CapabilityRegistryError(
                    f"capability dependency is not available: {dependency_id}"
                )

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS capability_schema (
                        version INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS capabilities (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        version TEXT NOT NULL,
                        status TEXT NOT NULL,
                        supports_json TEXT NOT NULL,
                        limitations_json TEXT NOT NULL,
                        claim_scopes_json TEXT NOT NULL,
                        dependencies_json TEXT NOT NULL,
                        health_check TEXT NOT NULL,
                        adopted_change TEXT,
                        rollback_target TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS capability_evidence (
                        id TEXT PRIMARY KEY,
                        capability_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        reference TEXT NOT NULL,
                        passed INTEGER NOT NULL,
                        observed_at TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(capability_id) REFERENCES capabilities(id)
                    );
                    CREATE TABLE IF NOT EXISTS capability_transitions (
                        id TEXT PRIMARY KEY,
                        capability_id TEXT NOT NULL,
                        from_status TEXT NOT NULL,
                        to_status TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        FOREIGN KEY(capability_id) REFERENCES capabilities(id)
                    );
                    """
                )
                version = connection.execute(
                    "SELECT version FROM capability_schema LIMIT 1"
                ).fetchone()
                if version is None:
                    connection.execute(
                        "INSERT INTO capability_schema VALUES (?)",
                        (self._SCHEMA_VERSION,),
                    )
                elif version["version"] != self._SCHEMA_VERSION:
                    raise CapabilityRegistryError(
                        f"unsupported capability schema version {version['version']}"
                    )
        except CapabilityRegistryError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise CapabilityRegistryError(
                f"could not initialize capability registry: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class PersistentSelfModel:
    """Build one read-only turn snapshot from persistent verified state."""

    def __init__(
        self,
        identity: CompanionIdentity,
        registry: SqliteCapabilityRegistry,
        *,
        runtime_capabilities: tuple[str, ...],
    ) -> None:
        self._identity = identity
        self._registry = registry
        self._runtime_capabilities = frozenset(runtime_capabilities)

    def snapshot(self) -> SelfModelSnapshot:
        available: list[SelfCapability] = []
        unavailable: list[SelfCapability] = []
        for capability in self._registry.list():
            runtime_ready = capability.id in self._runtime_capabilities
            status = capability.status if runtime_ready else "degraded"
            view = SelfCapability(
                id=capability.id,
                name=capability.name,
                version=capability.version,
                status=status,
                supports=capability.supports,
                limitations=(
                    capability.limitations
                    if runtime_ready
                    else capability.limitations + ("현재 interface runtime에 연결되지 않음",)
                ),
                claim_scopes=capability.claim_scopes,
                evidence=tuple(item.reference for item in capability.evidence if item.passed),
            )
            if capability.status == "available" and runtime_ready:
                available.append(view)
            else:
                unavailable.append(view)
        return SelfModelSnapshot(
            identity_name=self._identity.name,
            identity_version=self._identity.version,
            available=tuple(available),
            unavailable=tuple(unavailable),
        )


def _capability(
    row: sqlite3.Row, evidence_rows: list[sqlite3.Row]
) -> Capability:
    return Capability(
        id=row["id"],
        name=row["name"],
        version=row["version"],
        status=row["status"],
        supports=_tuple_json(row["supports_json"]),
        limitations=_tuple_json(row["limitations_json"]),
        claim_scopes=_tuple_json(row["claim_scopes_json"]),
        dependencies=_tuple_json(row["dependencies_json"]),
        health_check=row["health_check"],
        adopted_change=row["adopted_change"],
        rollback_target=row["rollback_target"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        evidence=tuple(
            CapabilityEvidence(
                id=item["id"],
                capability_id=item["capability_id"],
                kind=item["kind"],
                reference=item["reference"],
                passed=bool(item["passed"]),
                observed_at=item["observed_at"],
                created_at=item["created_at"],
            )
            for item in evidence_rows
        ),
    )


def _tuple_json(value: str) -> tuple[str, ...]:
    loaded = json.loads(value)
    if not isinstance(loaded, list) or any(not isinstance(item, str) for item in loaded):
        raise CapabilityRegistryError("capability list field is invalid")
    return tuple(loaded)


def _json(value: tuple[str, ...]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clean_items(items: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item.strip() for item in items if item.strip())


def _nonempty_items(items: tuple[str, ...], field: str) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(_clean_items(items)))
    if not cleaned:
        raise CapabilityRegistryError(f"capability {field} must not be empty")
    return cleaned


def _optional(value: str | None) -> str | None:
    return value.strip() if value is not None and value.strip() else None


def _now() -> str:
    return datetime.now(UTC).isoformat()
