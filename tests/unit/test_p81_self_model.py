"""P8.1 code capabilities and unchanged legacy context, using synthetic state."""

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from configs.dialogue.self_model import CAPABILITIES
import pytest
from test_capabilities import _identity
from test_context_assembly_baseline import capture
from test_schedule_dialogue import Harness

from companion.application.bootstrap import (
    TextRuntimeConfig,
    build_text_resources,
    build_text_runtime,
)
from companion.capabilities import PersistentSelfModel, SelfModelSnapshot, SqliteCapabilityRegistry
from companion.capability_admin import install_verified_foundation

HEADER = '[겨울이가 할 수 있는 것 — 사실만 말해]'


def test_exact_code_capabilities_and_hash():
    block = SelfModelSnapshot('', '', (), ()).system_message(companion=True)
    assert [name for name, _ in CAPABILITIES] == [
        '대화', '기억', '일정', '코드 확인', '웹 검색', '할 수 없는 것']
    assert block.splitlines() == [HEADER, *(
        f'{i}. {name}: {description}' for i, (name, description) in enumerate(CAPABILITIES, 1)
    ), '여기 없는 기능은 할 수 있다고 말하지 마.']
    assert hashlib.sha256(block.encode()).hexdigest() == (
        '2f23ecccb4bee90a5dd186d2a4441e2e62b41f64bf7d23b4b84e4579c3e9733b')
    assert '승인된 기억' not in block and '추론형 기억 후보' not in block
    assert '동작 방식' not in block and 'available' not in block


@pytest.mark.parametrize('policy', ['companion', 'legacy'])
def test_runtime_policy_uses_code_or_legacy_registry(tmp_path, policy):
    registry = SqliteCapabilityRegistry(tmp_path / 'capabilities.sqlite')
    install_verified_foundation(
        registry, test_reference='synthetic-test', observed_at='2026-08-19T00:00:00+00:00',
        conversation_health_reference='synthetic-chat', memory_health_reference='synthetic-memory',
    )
    provider = PersistentSelfModel(_identity(), registry,
                                   runtime_capabilities=('conversation', 'memory'))
    snapshot = provider.snapshot()
    config = TextRuntimeConfig(backend='fake', dialogue_policy=policy)
    resources = replace(build_text_resources(config), self_model_provider=provider)
    runtime = build_text_runtime(config, resources=resources)
    try:
        request = runtime.core._prepare('codex 쓸 수 있어?').request
        block = next(m.content for m in request.messages
                     if m.content.startswith((HEADER, '[검증된 SelfModel')))
        assert block == snapshot.system_message(companion=policy == 'companion')
        assert ('승인된 기억' in block) == (policy == 'legacy')
        assert ('추론형 기억 후보' in block) == (policy == 'legacy')
        # Registry contents and evidence are unchanged by prompt rendering.
        assert provider.snapshot() == snapshot
    finally:
        runtime.close()


def test_no_db_required_and_changed_registry_keeps_companion_prefix(tmp_path):
    h = Harness(tmp_path)
    try:
        first = h.runtime.core._prepare('codex 쓸 수 있어?').request
        at = next(i for i, m in enumerate(first.messages) if m.content.startswith(HEADER))
        h.runtime.core._self_model_provider = SimpleNamespace(
            snapshot=lambda: SelfModelSnapshot('different', 'changed', (), ()))
        second = h.runtime.core._prepare('어떤 기능이 있어?').request
        assert first.messages[:at + 1] == second.messages[:at + 1]
        assert at < next(i for i, m in enumerate(first.messages)
                         if m.content.startswith('일정 날짜 기준'))
    finally:
        h.runtime.close()


@pytest.mark.parametrize('case', ['full', 'explain', 'streaming'])
def test_companion_changes_only_self_model_at_existing_baseline_position(case):
    legacy, companion = capture(case), capture(case, companion_self_model=True)
    fixture = Path(__file__).parents[1] / 'fixtures/text-context-p81.json'
    baseline = json.loads(fixture.read_text())
    assert [hashlib.sha256(json.dumps(asdict(r), ensure_ascii=False, sort_keys=True).encode())
            .hexdigest() for r in companion] == baseline[case]
    assert len(legacy) == len(companion)
    for old, new in zip(legacy, companion, strict=True):
        at = next(i for i, m in enumerate(old.messages)
                  if m.content.startswith('[검증된 SelfModel'))
        assert new.messages[at].content.startswith(HEADER)
        restored = replace(new, messages=new.messages[:at] + (old.messages[at],)
                           + new.messages[at + 1:])
        assert restored == old  # All other prompt bytes, roles, order and sampling.
