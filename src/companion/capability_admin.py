"""Trusted bootstrap and audit CLI for Winter's Capability Registry."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
import os
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import urlopen

from companion.capabilities import (
    Capability,
    CapabilityRegistryError,
    SqliteCapabilityRegistry,
)
from companion.memory import MemoryRepositoryError, SqliteMemoryRepository


def install_verified_foundation(
    registry: SqliteCapabilityRegistry,
    *,
    test_reference: str,
    observed_at: str,
    conversation_health_reference: str,
    memory_health_reference: str,
) -> tuple[Capability, ...]:
    """Register only the two capabilities exercised by every current Core."""
    specs = (
        {
            "capability_id": "conversation",
            "name": "현재 대화",
            "version": "1",
            "supports": (
                "현재 대화에서 천우의 말에 답변할 수 있다",
                "CLI·Web·Voice가 같은 대화 기록을 사용한다",
            ),
            "limitations": (
                "대화가 없는 동안 화면 밖 활동을 하지 않는다",
                "외부 정보는 별도 근거 없이 안다고 말하지 않는다",
            ),
            "claim_scopes": ("conversation.respond",),
            "dependencies": (),
            "health_check": "local chat runtime and conversation storage",
            "health_reference": conversation_health_reference,
        },
        {
            "capability_id": "memory",
            "name": "승인된 기억",
            "version": "1",
            "supports": (
                "명시적으로 요청한 기억을 저장하고 다음 대화에서 사용할 수 있다",
                "근거가 있는 좁은 직접 진술과 상태 History를 보존한다",
            ),
            "limitations": (
                "모든 대화를 자동 영구 기억으로 만들지 않는다",
                "추론형 기억 후보는 품질 gate 전까지 활성화하지 않는다",
            ),
            "claim_scopes": ("memory.explicit",),
            "dependencies": ("conversation",),
            "health_check": "local SQLite memory repository",
            "health_reference": memory_health_reference,
        },
    )
    installed: list[Capability] = []
    for spec in specs:
        capability_id = str(spec["capability_id"])
        try:
            capability = registry.get(capability_id)
        except CapabilityRegistryError as error:
            if "not found" not in str(error):
                raise
            capability = registry.propose(
                capability_id=capability_id,
                name=str(spec["name"]),
                version=str(spec["version"]),
                supports=tuple(spec["supports"]),
                limitations=tuple(spec["limitations"]),
                claim_scopes=tuple(spec["claim_scopes"]),
                dependencies=tuple(spec["dependencies"]),
                health_check=str(spec["health_check"]),
            )
        registry.record_evidence(
            capability_id,
            kind="health",
            reference=str(spec["health_reference"]),
            passed=True,
            observed_at=observed_at,
        )
        registry.record_evidence(
            capability_id,
            kind="test",
            reference=test_reference,
            passed=True,
            observed_at=observed_at,
        )
        capability = registry.get(capability_id)
        if capability.status == "retired":
            raise CapabilityRegistryError(
                f"retired baseline capability requires a new version: {capability_id}"
            )
        for status in _path_to_available(capability.status):
            capability = registry.transition(capability_id, status)
        installed.append(capability)
    return tuple(installed)


def _path_to_available(status: str) -> tuple[str, ...]:
    return {
        "proposed": ("approved", "building", "testing", "available"),
        "approved": ("building", "testing", "available"),
        "building": ("testing", "available"),
        "testing": ("available",),
        "failed": ("building", "testing", "available"),
        "degraded": ("available",),
        "available": (),
    }.get(status, ())


def build_parser() -> argparse.ArgumentParser:
    data = Path(os.environ.get("WINTER_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=data / "capabilities.sqlite")
    parser.add_argument("--memory", type=Path, default=data / "memories.sqlite")
    parser.add_argument(
        "--model-url",
        default=os.environ.get("WINTER_LLM_URL", "http://127.0.0.1:18080"),
    )
    parser.add_argument("--install-foundation", action="store_true")
    parser.add_argument("--test-reference")
    parser.add_argument("--list", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.install_foundation == arguments.list:
        print("choose exactly one of --install-foundation or --list", file=sys.stderr)
        return 2
    try:
        registry = SqliteCapabilityRegistry(arguments.registry)
        if arguments.list:
            for capability in registry.list():
                print(
                    f"{capability.id}: {capability.status} v{capability.version}; "
                    f"evidence={len(capability.evidence)}"
                )
            return 0
        if not arguments.test_reference or not arguments.test_reference.strip():
            print("--test-reference is required for foundation install", file=sys.stderr)
            return 2
        _require_model_health(arguments.model_url)
        SqliteMemoryRepository(arguments.memory)
        observed_at = datetime.now(UTC).isoformat()
        installed = install_verified_foundation(
            registry,
            test_reference=arguments.test_reference.strip(),
            observed_at=observed_at,
            conversation_health_reference=f"health:{arguments.model_url.rstrip('/')}",
            memory_health_reference="health:sqlite-memory-open",
        )
    except (CapabilityRegistryError, MemoryRepositoryError, RuntimeError) as error:
        print(f"capability bootstrap unavailable: {error}", file=sys.stderr)
        return 1
    for capability in installed:
        print(f"{capability.id}: {capability.status} v{capability.version}")
    return 0


def _require_model_health(base_url: str) -> None:
    endpoint = f"{base_url.rstrip('/')}/health"
    try:
        with urlopen(endpoint, timeout=3) as response:
            if response.status != 200:
                raise RuntimeError(f"conversation health returned HTTP {response.status}")
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"conversation health unavailable at {endpoint}: {error}") from error


if __name__ == "__main__":
    raise SystemExit(main())
