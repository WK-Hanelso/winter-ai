from dataclasses import asdict
from io import StringIO
import json

from configs.dialogue.postfix_evaluation import SCENES
import experiments.text_context_ablation as runner
import pytest

from companion.contracts import ChatResult, ConversationMessage
from companion.grounding import GroundingPolicy
from companion.memory import MEMORY_BACKGROUND_INSTRUCTION


@pytest.mark.parametrize("scene", SCENES, ids=lambda s: s["id"])
def test_pairs_keep_exact_history_facts_and_budget(tmp_path, scene):
    pair = runner.prepare_pair(tmp_path, scene)
    a, b = pair.minimal, pair.full
    assert a.prompt == b.prompt == scene["turns"][-1]
    assert a.max_tokens == b.max_tokens
    assert a.max_tokens in (120, 224)
    identity = ConversationMessage("system", runner.IDENTITY.system_message())
    assert a.messages[0] == b.messages[0] == identity
    assert a.messages != b.messages
    assert tuple(m for m in a.messages if m.role != "system") == tuple(
        m for m in b.messages if m.role != "system"
    )
    # Identify blocks by role/header, not offsets that change when instructions split.
    def facts(request):
        return tuple(m for m in request.messages if m.role == "system" and m.content.startswith((
            MEMORY_BACKGROUND_INSTRUCTION,
            "다음은 현재 말과 관련 있어 검색된 오래된 실제 대화 발췌야.",
            "다음은 천우가 직접 남긴 아직 끝나지 않은 이야기야.",
        )))

    assert facts(a) == facts(b)
    instructions = []
    for request in (a, b):
        blocks = tuple(m for m in request.messages
                       if m.role == "system" and m != identity and m not in facts(request))
        instructions.append(blocks)
        history = tuple(m for m in request.messages if m.role != "system")[:-1]
        history_start = request.messages.index(history[0])
        history_end = request.messages.index(history[-1])
        assert request.messages[history_start:history_end + 1] == history
        for fact in facts(request):
            if fact.content.startswith(MEMORY_BACKGROUND_INSTRUCTION):
                assert request.messages.index(fact) < history_start
            else:
                assert history_end < request.messages.index(fact) < len(request.messages) - 1
        assert request.messages[-1] == ConversationMessage("user", request.prompt)
        if request is a:
            assert all(request.messages.index(m) < history_start for m in blocks)
        else:
            fixed = tuple(m for m in blocks if request.messages.index(m) < history_start)
            act = tuple(m for m in blocks if history_end < request.messages.index(m))
            assert len(fixed) == 2 and len(act) == 1
            assert fixed[0].content.startswith("한국어 반말로 말해.")
            assert fixed[1] == ConversationMessage("system", GroundingPolicy().instruction())
            assert request.messages[history_end + 1] == act[0]
    assert instructions[0] == (ConversationMessage("system", runner.MINIMAL_INSTRUCTION),)
    assert len(instructions[1]) == 3
    assert tuple(m for m in a.messages if m not in instructions[0]) == tuple(
        m for m in b.messages if m not in instructions[1]
    )
    for expected in scene["expect"]:
        assert expected not in json.dumps(asdict(a), ensure_ascii=False)
        assert expected not in json.dumps(asdict(b), ensure_ascii=False)
    if scene["id"] == "known_memory":
        assert pair.evidence.memory_contents == ("나는 결론부터 듣는 설명을 좋아해",)
        for request in (a, b):
            assert any(m.role == "system" and m.content.endswith(
                "\n나는 결론부터 듣는 설명을 좋아해.") for m in request.messages)
    else:
        assert pair.evidence.memory_contents == ()


class ObservedStub:
    def __init__(self):
        self.seed = 0
        self.calls = []
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        self.calls.append({"text": "내가 기억하기로 네가 좋아하는 음식은 피자야."})
        return ChatResult(self.calls[-1]["text"])


def test_run_is_raw_audit_without_repair_and_rotates_order(monkeypatch):
    records = []
    monkeypatch.setattr(runner, "emit", lambda out, row: records.append(dict(row)))
    model = ObservedStub()
    runner.run(StringIO(), model, scenes=(SCENES[3],), seeds=(1, 2, 3))
    assert len(model.requests) == len(records) == 6
    assert [r["arm"] for r in records] == ["minimal", "full", "full", "minimal", "minimal", "full"]
    assert all(not r["repair_executed"] and r["audit_only"] for r in records)
    assert all("피자" in r["raw"] and r["human_review"] == "pending" for r in records)
    assert [r.seed for r in model.requests] == [1, 1, 2, 2, 3, 3]
    for seed in (1, 2, 3):
        pair = [r for r in records if r["seed"] == seed]
        assert pair[0]["evidence"] == pair[1]["evidence"]


def test_failed_generation_is_recorded_and_no_fallback(monkeypatch):
    class Failing(ObservedStub):
        def generate(self, request):
            raise RuntimeError("offline failure")

    records = []
    monkeypatch.setattr(runner, "emit", lambda out, row: records.append(row))
    with pytest.raises(RuntimeError, match="offline failure"):
        runner.run(StringIO(), Failing(), scenes=(SCENES[0],))
    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert "raw" not in records[0]


def test_source_manifest_includes_runner_and_core():
    hashes = runner.source_hashes()
    assert "src/companion/core.py" in hashes
    assert "experiments/text_context_ablation.py" in hashes
    assert all(len(value) == 64 for value in hashes.values())
