"""Replay public synthetic #132 outputs, never personal DBs or a live model."""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any

from experiments.text_policy_eval import make_core
import pytest

from companion.adapters.fake import InMemoryConversationRepository
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.grounding import ClaimEvidence, ResponseClaimGate
from companion.memory import SqliteMemoryRepository
from companion.speech_segments import split_sentences

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evaluations/132-text-policy"
MODES = ("plain", "stream", "no-stream")


class ReplayModel:
    def __init__(self, *answers: str) -> None:
        self.answers = iter(answers)
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        return ChatResult(next(self.answers))


class StreamingReplayModel(ReplayModel):
    closed = False

    def generate_stream(self, request: ChatRequest) -> Iterator[str]:
        self.requests.append(request)
        try:
            # Chunk boundaries must not become content/length decisions.
            yield from next(self.answers)
        finally:
            self.closed = True


def respond(core: CompanionCore, user: str, mode: str) -> tuple[str, list[str]]:
    handed: list[str] = []
    if mode == "plain":
        response = core.respond_to_text(user)
    else:
        response = core.respond_to_text_streaming(user, handed.append)
        assert handed == split_sentences(response.text)
    return response.text, handed


def model_for(mode: str, *answers: str) -> ReplayModel:
    cls = StreamingReplayModel if mode == "stream" else ReplayModel
    return cls(*answers)


def recorded_turn(filename: str, scene: str, policy: str) -> dict[str, Any]:
    for line in (EVIDENCE / filename).read_text().splitlines():
        row: dict[str, Any] = json.loads(line)
        if (row.get("scene"), row.get("policy"), row.get("turn_index")) == (
            scene, policy, 1
        ):
            return row
    raise AssertionError("missing frozen #132 evidence")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("filename,scene,policy", [
    ("reserved-rollout.jsonl", "reserved_mixed", "contextual-moves-only"),
    ("development-rollout-sequential.jsonl", "dev_mixed", "legacy"),
])
def test_actual_132_draft_survives_core_without_content_or_format_loss(
    tmp_path: Path, mode: str, filename: str, scene: str, policy: str
) -> None:
    row = recorded_turn(filename, scene, policy)
    call = row["calls"][0]
    raw = call["text"]
    assert call["finish_reason"] == "stop"
    assert row["response"]["text"] != raw
    prefix = tuple(
        ConversationMessage(m["role"], m["content"])
        for m in call["request"]["messages"][:-1] if m["role"] != "system"
    )
    model = model_for(mode, raw)
    core, gate = make_core(tmp_path, model, policy, row["seed"], prefix)
    text, _ = respond(core, row["user"], mode)
    assert text == raw
    assert len(model.requests) == 1
    assert model.requests[0].max_tokens == call["request"]["max_tokens"]
    assert gate.checks  # Production rules remain active, not stubbed out.
    if isinstance(model, StreamingReplayModel):
        assert model.closed


@pytest.mark.parametrize("mode", MODES)
def test_late_unsafe_claim_in_draft_and_repair_cannot_hide_after_two_sentences(
    mode: str,
) -> None:
    unsafe = "첫 문장이야. 둘째 문장이야. 나도 오늘은 따뜻한 차 한 잔을 꼭 마셔야겠어."
    repair = "알겠어. 다시 말할게. 나도 오늘은 따뜻한 차 한 잔을 꼭 마셔야겠어."
    model = model_for(mode, unsafe, repair)
    repository = InMemoryConversationRepository()
    core = CompanionCore(model, repository)
    text, handed = respond(core, "오늘 어때?", mode)
    gate = ResponseClaimGate()
    evidence = ClaimEvidence(user_text="오늘 어때?")
    assert gate.violations(repair, evidence)[0].kind == "body_action"
    # #135 fixes the measured re-flag without bypassing final review.
    assert text == gate.fallback(gate.violations(repair, evidence))
    assert gate.violations(text, evidence) == ()
    assert "따뜻한 차" not in " ".join(handed)
    assert len(model.requests) == 2
    assert any(m.role == "assistant" and m.content == unsafe for m in model.requests[1].messages)
    assert repository.list_messages()[-1].content == text


@pytest.mark.parametrize("mode", MODES)
def test_valid_repair_is_not_reduced_to_an_intro(mode: str) -> None:
    repair = "알겠어. 다시 말할게.\n\n나는 기억을 지키는 쪽을 고를래. 이유는 연속성이야."
    model = model_for(mode, "상황에 따라 달라. 둘 다 중요해.", repair)
    repository = InMemoryConversationRepository()
    text, _ = respond(CompanionCore(model, repository), "둘 중 뭐가 더 낫다고 생각해?", mode)
    assert text == repair
    assert len(model.requests) == 2
    assert repository.list_messages()[-1].content == repair


@pytest.mark.parametrize("mode", ("stream", "no-stream"))
def test_final_gate_result_is_delivered_not_the_pre_finalization_draft(mode: str) -> None:
    class FinalGate(ResponseClaimGate):
        calls = 0

        def violations(self, text: str, evidence: ClaimEvidence):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 2:
                return super().violations("나는 그동안 책을 읽고 있을게.", evidence)
            return ()

    model = model_for(mode, "첫 문장이야. 둘째 문장이야. 셋째 문장이야.")
    repository = InMemoryConversationRepository()
    core = CompanionCore(model, repository, response_claim_gate=FinalGate())
    text, handed = respond(core, "오늘 어때?", mode)
    assert "첫 문장이야" not in text
    assert handed == split_sentences(text)
    assert repository.list_messages()[-1].content == text


@pytest.mark.parametrize("mode", MODES)
def test_memory_ack_is_finalized_and_delivered_without_model_call(
    tmp_path: Path, mode: str,
) -> None:
    model = model_for(mode)  # Any model call exhausts this explicit test double.
    repository = InMemoryConversationRepository()
    memory = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    core = CompanionCore(model, repository, memory_repository=memory)
    text, _ = respond(core, "기억해. 나는 짧은 설명을 좋아해", mode)
    assert text == "기억해둘게."
    assert repository.list_messages()[-1].content == text
    assert model.requests == []


@pytest.mark.parametrize("mode", MODES)
def test_internal_whitespace_and_code_blocks_are_not_flattened(mode: str) -> None:
    raw = (
        "설정 예시야.\n\n```python\nvalues = [1.2, 3.4]\n"
        "for value in values:\n    print(value)\n```\n\n여기까지야."
    )
    repository = InMemoryConversationRepository()
    text, _ = respond(CompanionCore(model_for(mode, raw), repository), "설정 예시 설명해줘", mode)
    assert text == raw
    assert repository.list_messages()[-1].content == raw


def test_failed_stream_is_closed_and_emits_no_unreviewed_partial_answer() -> None:
    class FailingStream(StreamingReplayModel):
        def generate_stream(self, request: ChatRequest) -> Iterator[str]:
            try:
                yield "첫 문장이야. 둘째 문장이야. 셋째 문장이야. "
                raise RuntimeError("stream failure")
            finally:
                self.closed = True

    model = FailingStream()
    repository = InMemoryConversationRepository()
    handed: list[str] = []
    core = CompanionCore(model, repository)
    with pytest.raises(RuntimeError, match="stream failure"):
        core.respond_to_text_streaming("오늘 어때?", handed.append)
    assert model.closed
    assert handed == []
    assert [m.role for m in repository.list_messages()] == ["user"]
