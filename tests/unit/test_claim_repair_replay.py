"""Frozen output replay measures gates, not what a newly prompted LLM will say."""

import json
from pathlib import Path

from experiments.text_policy_eval import make_core
import pytest

from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.grounding import ClaimEvidence, ResponseClaimGate

TRACE = Path(__file__).parents[2] / "docs/evaluations/132-text-policy/development-fixed.jsonl"


def record(policy: str) -> dict:
    return next(
        row for row in map(json.loads, TRACE.read_text().splitlines())
        if row.get("scene") == "dev_memory" and row.get("policy") == policy
    )


class RecordedModel:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        return ChatResult(next(self.answers))


@pytest.mark.parametrize("policy,expected,calls", (
    ("contextual", None, 1),
    ("stable-register-only", "그건 지금 내 기억 근거에는 없어.", 2),
    ("contextual-moves-only", "그건 지금 내 기억 근거에는 없어.", 2),
))
def test_real_132_outputs_through_new_core(
    tmp_path: Path, policy: str, expected: str | None, calls: int,
) -> None:
    row = record(policy)
    model = RecordedModel([c["text"] for c in row["calls"]])
    prefix = tuple(
        ConversationMessage(m["role"], m["content"])
        for m in row["calls"][0]["request"]["messages"][:-1] if m["role"] != "system"
    )
    core, gate = make_core(tmp_path, model, policy, row["seed"], prefix)
    response = core.respond_to_text(row["user"])
    assert response.text == (expected or row["calls"][0]["text"])
    assert response.text != row["response"]["text"]
    assert len(model.requests) == calls
    assert gate.violations(response.text, ClaimEvidence(user_text=row["user"])) == ()
    if calls == 2:
        assert model.requests[1].prompt == row["user"]
        repair_instruction = model.requests[1].messages[-1].content
        assert "질문의 대상·주체·목적을 바꾸지 마" in repair_instruction
        assert "서론만 내놓지 마" in repair_instruction


def test_real_legacy_unstated_history_is_now_detected() -> None:
    row = record("legacy")
    violations = ResponseClaimGate().violations(
        row["calls"][0]["text"], ClaimEvidence(user_text=row["user"]),
    )
    assert [v.kind for v in violations] == ["memory"]


def test_question_does_not_supply_evidence_for_its_own_presupposition() -> None:
    gate = ResponseClaimGate()
    evidence = ClaimEvidence(user_text="내가 김치찌개를 좋아한다고 했지?")
    assert gate.violations("네가 김치찌개를 좋아한다고 했지.", evidence)[0].kind == "memory"
