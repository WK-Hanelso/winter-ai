from io import StringIO
import json

from experiments.text_policy_eval import (
    IDENTITY,
    ObservedLocalModel,
    fixed_prefix,
    make_core,
    run,
)
import pytest

from companion.adapters.fake import FakeChatModel
from companion.contracts import ChatRequest, ChatResult, ConversationMessage


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://evil.test",
        "http://u:p@127.0.0.1",
        "http://127.0.0.1/?secret=value",
        "http://127.0.0.1/x",
    ],
)
def test_runner_rejects_external_or_credentialed_urls(url):
    with pytest.raises(ValueError):
        ObservedLocalModel(url, 1)


def test_fixed_prefix_is_identical_and_contains_no_rubric():
    assert fixed_prefix(("이전 질문", "현재 질문")) == (
        ConversationMessage("user", "이전 질문"),
        ConversationMessage("assistant", "응, 듣고 있어."),
    )


def test_core_uses_isolated_state_and_synthetic_identity(tmp_path):
    first, _ = make_core(tmp_path / "first", FakeChatModel(), "legacy", 13201)
    second, _ = make_core(tmp_path / "second", FakeChatModel(), "contextual", 13201)
    saved = first.respond_to_text("기억해. Python config를 선호해")
    assert saved.memory_candidate_ids
    assert second._memory_repository.list() == ()
    assert first._identity == second._identity == IDENTITY
    assert first._read_only_worker is None and second._owned_work_executor is None


def test_http_observer_preserves_payload_and_captures_wire_results():
    model = ObservedLocalModel("http://127.0.0.1:18082", 13201)
    sent = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(
                {
                    "choices": [{"message": {"content": "알겠어."}, "finish_reason": "stop"}],
                    "usage": {"completion_tokens": 4},
                }
            ).encode()

    class Opener:
        def open(self, request, **kwargs):
            sent.append(json.loads(request.data))
            return Reply()

    model.opener = Opener()
    request = ChatRequest("hi", max_tokens=120)
    assert model.generate(request) == ChatResult("알겠어.")
    assert sent[0] == json.loads(
        model.adapter._payload(ChatRequest("hi", max_tokens=120, seed=13201), stream=False)
    )
    assert model.calls[0]["finish_reason"] == "stop"
    assert model.calls[0]["request"]["seed"] == 13201


def test_failure_is_recorded_and_stops_the_batch(monkeypatch, tmp_path):
    import experiments.text_policy_eval as runner

    class FailedModel:
        calls = []

        def generate(self, request):
            raise RuntimeError("synthetic disconnect")

    monkeypatch.setattr(
        runner, "SCENES", (dict(id="one", split="development", turns=("first", "second")),)
    )
    output = StringIO()
    monkeypatch.setattr(runner, "emit", lambda sink, record: sink.write(json.dumps(record) + "\n"))
    with pytest.raises(RuntimeError, match="synthetic disconnect"):
        run(
            output,
            FailedModel(),
            split="development",
            mode="fixed",
            policies=("legacy", "contextual"),
            seed=1,
        )
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(records) == 1 and records[0]["status"] == "failed"
    assert records[0]["human_review"] == "pending"


def test_postfix_suite_extends_regressions_without_relabeling_heldout():
    from configs.dialogue.evaluation import SCENES
    from configs.dialogue.postfix_evaluation import SCENES as POSTFIX

    assert POSTFIX[:5] == tuple(s for s in SCENES if s["split"] == "development")
    assert len(POSTFIX) == 7
    assert sum(len(s["turns"]) for s in POSTFIX) * 2 == 28
    assert all(s["split"] == "development" for s in POSTFIX)
    assert POSTFIX[5]["turns"][0].startswith("기억해.")


def test_selected_rollout_records_memory_ack_and_keeps_policies_isolated(monkeypatch, tmp_path):
    from configs.dialogue.postfix_evaluation import SCENES
    import experiments.text_policy_eval as runner

    class OfflineObserved:
        calls = []

        def generate(self, request):
            assert "expect" not in str(request)
            assert "저장된 선호를 회상한다" not in str(request)
            return ChatResult("응, 결론부터 듣는 설명을 좋아한다고 기억해.")

    records = []
    monkeypatch.setattr(runner, "emit", lambda sink, row: records.append(row))
    run(
        StringIO(), OfflineObserved(), split="development", mode="rollout",
        policies=("legacy", "contextual"), seed=13201, scenes=(SCENES[5],),
    )
    assert len(records) == 4
    acks = [r for r in records if r["turn_index"] == 0]
    recalls = [r for r in records if r["turn_index"] == 1]
    assert all(r["response"]["memory_candidate_ids"] for r in acks)
    assert (
        acks[0]["response"]["memory_candidate_ids"]
        != acks[1]["response"]["memory_candidate_ids"]
    )
    assert all("결론부터" in r["response"]["text"] for r in recalls)
