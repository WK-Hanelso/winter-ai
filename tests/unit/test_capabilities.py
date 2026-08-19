from pathlib import Path

import pytest

from companion.capabilities import (
    CapabilityRegistryError,
    PersistentSelfModel,
    SqliteCapabilityRegistry,
)
from companion.capability_admin import install_verified_foundation
from companion.identity import CompanionIdentity


def _identity() -> CompanionIdentity:
    return CompanionIdentity(
        name="겨울이",
        role="천우와 대화하는 동반자",
        core_personality=("담백함",),
        values=("정직",),
        relationship_policy=("추측하지 않는다",),
        immutable_boundaries=("권한을 지어내지 않는다",),
        version="1.0",
    )


def _propose(
    registry: SqliteCapabilityRegistry,
    capability_id: str = "conversation",
    *,
    dependencies: tuple[str, ...] = (),
):
    return registry.propose(
        capability_id=capability_id,
        name="현재 대화",
        version="1",
        supports=("현재 대화에서 답할 수 있다",),
        limitations=("대화 밖에서 활동하지 않는다",),
        claim_scopes=("conversation.respond",),
        dependencies=dependencies,
        health_check="runtime:core",
    )


def _advance_to_testing(
    registry: SqliteCapabilityRegistry, capability_id: str
) -> None:
    registry.transition(capability_id, "approved")
    registry.transition(capability_id, "building")
    registry.transition(capability_id, "testing")


def _make_available(
    registry: SqliteCapabilityRegistry, capability_id: str
) -> None:
    registry.record_evidence(
        capability_id,
        kind="health",
        reference="runtime:healthy",
        passed=True,
        observed_at="2026-08-19T00:00:00+00:00",
    )
    registry.record_evidence(
        capability_id,
        kind="test",
        reference="pytest:shared-core",
        passed=True,
        observed_at="2026-08-19T00:01:00+00:00",
    )
    _advance_to_testing(registry, capability_id)
    registry.transition(capability_id, "available")


def test_registry_persists_evidence_and_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.sqlite"
    registry = SqliteCapabilityRegistry(path)
    _propose(registry)
    _make_available(registry, "conversation")

    reopened = SqliteCapabilityRegistry(path)
    capability = reopened.get("conversation")

    assert capability.status == "available"
    assert capability.claim_scopes == ("conversation.respond",)
    assert [(item.kind, item.passed) for item in capability.evidence] == [
        ("health", True),
        ("test", True),
    ]


def test_available_requires_latest_health_and_test_evidence(tmp_path: Path) -> None:
    registry = SqliteCapabilityRegistry(tmp_path / "capabilities.sqlite")
    _propose(registry)
    registry.record_evidence(
        "conversation",
        kind="health",
        reference="runtime:down",
        passed=False,
        observed_at="2026-08-19T00:00:00+00:00",
    )
    registry.record_evidence(
        "conversation",
        kind="test",
        reference="pytest:passed",
        passed=True,
        observed_at="2026-08-19T00:01:00+00:00",
    )
    _advance_to_testing(registry, "conversation")

    with pytest.raises(CapabilityRegistryError, match="health"):
        registry.transition("conversation", "available")


def test_available_requires_available_dependencies(tmp_path: Path) -> None:
    registry = SqliteCapabilityRegistry(tmp_path / "capabilities.sqlite")
    _propose(registry, "conversation")
    _propose(registry, "memory", dependencies=("conversation",))
    registry.record_evidence(
        "memory",
        kind="health",
        reference="runtime:memory",
        passed=True,
        observed_at="2026-08-19T00:00:00+00:00",
    )
    registry.record_evidence(
        "memory",
        kind="evaluation",
        reference="eval:memory",
        passed=True,
        observed_at="2026-08-19T00:01:00+00:00",
    )
    _advance_to_testing(registry, "memory")

    with pytest.raises(CapabilityRegistryError, match="dependency"):
        registry.transition("memory", "available")


def test_invalid_transition_and_duplicate_proposal_fail_closed(tmp_path: Path) -> None:
    registry = SqliteCapabilityRegistry(tmp_path / "capabilities.sqlite")
    _propose(registry)

    with pytest.raises(CapabilityRegistryError, match="cannot transition"):
        registry.transition("conversation", "available")
    with pytest.raises(CapabilityRegistryError, match="already exists"):
        _propose(registry)


def test_self_model_only_authorizes_available_runtime_capabilities(
    tmp_path: Path,
) -> None:
    registry = SqliteCapabilityRegistry(tmp_path / "capabilities.sqlite")
    _propose(registry)
    _make_available(registry, "conversation")

    active = PersistentSelfModel(
        _identity(), registry, runtime_capabilities=("conversation",)
    ).snapshot()
    disconnected = PersistentSelfModel(
        _identity(), registry, runtime_capabilities=()
    ).snapshot()

    assert active.authorized_claim_scopes == ("conversation.respond",)
    assert active.available[0].status == "available"
    assert "현재 대화에서 답할 수 있다" in active.system_message()
    assert disconnected.authorized_claim_scopes == ()
    assert disconnected.unavailable[0].status == "degraded"
    assert "현재 interface runtime에 연결되지 않음" in disconnected.system_message()


def test_verified_foundation_install_is_persistent_and_idempotent(
    tmp_path: Path,
) -> None:
    registry = SqliteCapabilityRegistry(tmp_path / "capabilities.sqlite")

    first = install_verified_foundation(
        registry,
        test_reference="pytest:724",
        observed_at="2026-08-19T00:00:00+00:00",
        conversation_health_reference="health:conversation",
        memory_health_reference="health:memory",
    )
    second = install_verified_foundation(
        registry,
        test_reference="pytest:724",
        observed_at="2026-08-19T00:05:00+00:00",
        conversation_health_reference="health:conversation",
        memory_health_reference="health:memory",
    )

    assert [(item.id, item.status) for item in first] == [
        ("conversation", "available"),
        ("memory", "available"),
    ]
    assert [(item.id, item.status) for item in second] == [
        ("conversation", "available"),
        ("memory", "available"),
    ]
    assert len(SqliteCapabilityRegistry(tmp_path / "capabilities.sqlite").list()) == 2
