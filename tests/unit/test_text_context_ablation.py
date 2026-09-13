from dataclasses import asdict
from io import StringIO
import json

from configs.dialogue.postfix_evaluation import SCENES
import experiments.text_context_ablation as runner
import pytest

from companion.contracts import ChatResult


@pytest.mark.parametrize("scene", SCENES, ids=lambda s: s["id"])
def test_pairs_keep_exact_history_facts_and_budget(tmp_path, scene):
    pair = runner.prepare_pair(tmp_path, scene)
    a, b = pair.minimal, pair.full
    assert a.prompt == b.prompt == scene["turns"][-1]
    assert a.max_tokens == b.max_tokens
    assert a.max_tokens in (120, 224)
    assert a.messages[0] == b.messages[0]
    assert a.messages != b.messages
    assert tuple(m for m in a.messages if m.role != "system") == tuple(
        m for m in b.messages if m.role != "system"
    )
    # Instructions now precede the identical selected facts and real history.
    assert a.messages[2:] == b.messages[3:]
    assert a.messages[1].content == runner.MINIMAL_INSTRUCTION
    for expected in scene["expect"]:
        assert expected not in json.dumps(asdict(a), ensure_ascii=False)
        assert expected not in json.dumps(asdict(b), ensure_ascii=False)
    if scene["id"] == "known_memory":
        assert pair.evidence.memory_contents == ("나는 결론부터 듣는 설명을 좋아해",)
        for request in (a, b):
            assert any("[선호]" in m.content for m in request.messages)
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
