"""Locked synthetic model inputs captured from pre-extraction commit 393bc4c."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import random

import pytest

from companion.adapters.fake import FailingChatModel, InMemoryConversationRepository
from companion.beliefs import Belief
from companion.capabilities import SelfCapability, SelfModelSnapshot
from companion.context import ConversationContextBuilder
from companion.contracts import ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.day_reflection import DayReflectionPoint, WinterDayReflection
from companion.dialogue.character_snapshot import CharacterSnapshot
from companion.dialogue.context_assembler import ContextAssembler
from companion.identity import CompanionIdentity
from companion.improvements import ImprovementSnapshot, ImprovementSummary
from companion.memory import Memory
from companion.open_loops import OpenLoop
from companion.verbal_style import VerbalStylePlanner

CASES = ("minimal", "bounded", "full", "explain", "streaming", "repair")
FIXTURE = Path(__file__).parents[1] / "fixtures/text-context-393bc4c.json"
STAMP = "2026-09-01T00:00:00+00:00"


class StaticState:
    def __init__(self, value, history=()):
        self.value = value
        self.history = history

    def retrieve(self, *args, **kwargs):
        return self.value

    def retrieve_current_state_history(self, query):
        return self.history

    def snapshot(self):
        return self.value


class FixedClock:
    def now(self):
        return datetime(2026, 9, 10, tzinfo=UTC)


class SpyModel:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ChatResult("알겠어.")

    def generate_stream(self, request):
        self.requests.append(request)
        yield "알겠어."


class RepairOnce:
    def repair_instruction(self, behavior, draft, **kwargs):
        return "synthetic repair instruction" if draft == "초안." else None


def synthetic_core(model, *, full=False, bounded=True, **overrides):
    memory = Memory(
        "memory-1", "preference", "Python config를 선호해", 3, 1.0, "active",
        "explicit", STAMP, STAMP, None, None, "conversation:1", "durable",
    )
    historical = Memory(
        "memory-2", "semantic", "예전에는 YAML을 썼어", 3, 1.0, "deprecated",
        "explicit", STAMP, STAMP, None, None, "conversation:2", "current",
    )
    identity = CompanionIdentity(
        "겨울이", "synthetic companion", ("차분함",), ("정직함",),
        ("함께 대화한다",), ("근거를 지어내지 않는다",), "fixture-v1",
    )
    capability = SelfCapability(
        "conversation", "텍스트 대화", "1", "available", ("대화",),
        ("외부 실행 불가",), ("conversation",), ("synthetic-test",),
    )
    options = {}
    if full:
        options = dict(
            identity=identity,
            memory_retriever=StaticState((memory,), (historical,)),
            belief_retriever=StaticState((Belief(
                "belief-1", "설정", "명시적인 설정을 선호해", "검토하기 쉬워", 0.8,
                ("conversation:1",), "active", "review", STAMP, STAMP, None,
            ),)),
            open_loop_retriever=StaticState((OpenLoop(
                "loop-1", "설정 비교를 이어가자", "open", "explicit", STAMP, STAMP, None,
            ),)),
            self_model_provider=StaticState(SelfModelSnapshot("겨울이", "fixture-v1",
                                                             (capability,), ())),
            improvement_snapshot_provider=StaticState(ImprovementSnapshot((ImprovementSummary(
                "improve-1", "설정 비교", "설명 부족", "discussing", None, False,
            ),))),
            day_reflection_retriever=StaticState((WinterDayReflection(
                "day-1", "2026-09-01", "fixture-hash", ("event-1",), (1,),
                (DayReflectionPoint("shared_moment", "설정을 함께 검토했다", (1,)),), STAMP,
            ),)),
            clock=FixedClock(),
            conversation_recall_retriever=StaticState((
                ConversationMessage("user", "예전에 설정을 비교했어"),
                ConversationMessage("assistant", "장단점을 정리했지."),
            )),
        )
    options.update(overrides)
    repository = InMemoryConversationRepository(_messages=[
        ConversationMessage("user", "이전 질문"),
        ConversationMessage("assistant", "이전 답변"),
    ])
    return CompanionCore(
        model, repository,
        ConversationContextBuilder(max_messages=2, max_characters=100) if bounded else None,
        verbal_style_planner=VerbalStylePlanner(rng=random.Random(130)),
        **options,
    ), repository


def capture(case):
    model = SpyModel()
    core, repository = synthetic_core(model, full=case in ("full", "explain", "streaming"),
                                      bounded=case != "minimal")
    text = "원리를 자세히 설명해줘" if case == "explain" else "설정 이야기를 해볼까?"
    if case == "repair":
        core._response_reviewer = RepairOnce()
        core._review_and_repair(core._prepare(text), "초안.")
    elif case == "streaming":
        core.respond_to_text_streaming(text, lambda sentence: None)
    else:
        core.respond_to_text(text)
    return model.requests


def fingerprints():
    return {
        case: [hashlib.sha256(json.dumps(asdict(request), ensure_ascii=False,
                                        sort_keys=True).encode()).hexdigest()
               for request in capture(case)]
        for case in CASES
    }


@pytest.mark.parametrize("case", CASES)
def test_requests_match_pre_extraction_baseline(case):
    baseline = json.loads(FIXTURE.read_text())
    actual = [hashlib.sha256(json.dumps(asdict(request), ensure_ascii=False,
                                      sort_keys=True).encode()).hexdigest()
              for request in capture(case)]
    assert actual == baseline[case]


def test_full_request_preserves_system_block_order_and_evidence():
    request = capture("full")[0]
    blocks = [message.content for message in request.messages if message.role == "system"]
    assert len(blocks) == 11
    markers = ["너는 겨울이야", "SelfModel", "개선 안건", "지난날 회고", "memory-1",
               "예전에는 YAML을 썼어", "belief-1", "예전에 설정을 비교했어", "loop-1",
               "문장 이내로", "지어내지 마"]
    for block, marker in zip(blocks, markers, strict=True):
        assert marker in block
    assert request.messages[-2:] == (
        ConversationMessage("assistant", "이전 답변"),
        ConversationMessage("user", "설정 이야기를 해볼까?"),
    )


class RecordingAssembler(ContextAssembler):
    def __init__(self):
        self.inputs = []
        self.repairs = []

    def assemble(self, **kwargs):
        self.inputs.append(kwargs)
        return super().assemble(**kwargs)

    def repair_request(self, request, draft, instruction):
        self.repairs.append((request, draft, instruction))
        return super().repair_request(request, draft, instruction)


def test_snapshot_preserves_values_and_evidence_without_mutation():
    assembler = RecordingAssembler()
    core, _ = synthetic_core(SpyModel(), full=True, context_assembler=assembler)
    core.respond_to_text("설정 이야기를 해볼까?")
    inputs = assembler.inputs[0]
    snapshot = inputs["snapshot"]
    before = asdict(snapshot)
    assert snapshot.identity.version == "fixture-v1"
    assert snapshot.self_model.available[0].evidence == ("synthetic-test",)
    assert snapshot.capability_claim_scopes == ("conversation",)
    assert snapshot.improvement_statuses == ("discussing",)
    assert snapshot.beliefs[0].evidence == ("conversation:1",)
    assert snapshot.open_loops[0].id == "loop-1"
    assert snapshot.prior_days[0].points[0].evidence_message_ids == (1,)
    assert inputs["memories"][0].evidence_ref == "conversation:1"
    assert inputs["current_state_history"][0].evidence_ref == "conversation:2"
    with pytest.raises(FrozenInstanceError):
        snapshot.identity = None
    with pytest.raises(FrozenInstanceError):
        snapshot.beliefs[0].stance = "changed"
    # Pure assembly is repeatable and retains the selected objects, not new records.
    assert assembler.assemble(**inputs) == assembler.assemble(**inputs)
    assert asdict(snapshot) == before


def test_new_turn_reads_updated_state_without_rewriting_previous_snapshot():
    assembler = RecordingAssembler()
    core, _ = synthetic_core(SpyModel(), full=True, context_assembler=assembler)
    core.respond_to_text("첫 질문")
    first = assembler.inputs[0]["snapshot"]
    revised = replace(first.self_model, available=(), unavailable=first.self_model.available)
    core._self_model_provider.value = revised
    core.respond_to_text("다음 질문")
    second = assembler.inputs[1]["snapshot"]
    assert first.capability_claim_scopes == ("conversation",)
    assert second.capability_claim_scopes == ()
    assert second.self_model.unavailable == first.self_model.available


def test_repair_uses_same_context_without_reading_state_again():
    assembler = RecordingAssembler()
    model = SpyModel()
    core, _ = synthetic_core(model, full=True, context_assembler=assembler,
                            response_reviewer=RepairOnce())
    turn = core._prepare("설정 이야기를 해볼까?")
    assert len(assembler.inputs) == 1
    core._review_and_repair(turn, "초안.")
    assert len(assembler.inputs) == 1
    assert assembler.repairs == [(turn.request, "초안.", "synthetic repair instruction")]
    assert model.requests[0].messages[:-2] == turn.request.messages


def test_missing_instructions_preserve_existing_empty_final_block():
    assembler = ContextAssembler()
    recent = (ConversationMessage("user", "hello"),)
    request = assembler.assemble(snapshot=CharacterSnapshot(), text="hello",
                                 recent_messages=recent, grounding_instruction=None,
                                 dialogue_instruction="", max_tokens=120)
    assert request.messages == (ConversationMessage("system", ""),) + recent
    assert request.max_tokens == 120


def test_assembly_failure_propagates_without_model_call_or_fake_response():
    class BrokenAssembler(ContextAssembler):
        def assemble(self, **kwargs):
            raise ValueError("synthetic assembly failure")

    model = SpyModel()
    core, repository = synthetic_core(model, context_assembler=BrokenAssembler())
    with pytest.raises(ValueError, match="synthetic assembly failure"):
        core.respond_to_text("현재 질문")
    assert model.requests == []
    assert repository.list_messages()[-1] == ConversationMessage("user", "현재 질문")
    assert len(repository.list_messages()) == 3


def test_state_read_failure_is_not_replaced_by_empty_snapshot():
    class BrokenProvider:
        def snapshot(self):
            raise RuntimeError("synthetic state failure")

    assembler = RecordingAssembler()
    model = SpyModel()
    core, repository = synthetic_core(model, context_assembler=assembler,
                                     self_model_provider=BrokenProvider())
    with pytest.raises(RuntimeError, match="synthetic state failure"):
        core.respond_to_text("현재 질문")
    assert assembler.inputs == []
    assert model.requests == []
    assert repository.list_messages()[-1] == ConversationMessage("user", "현재 질문")


def test_model_failure_preserves_current_user_turn_after_assembly():
    from companion.adapters.fake import AdapterUnavailableError

    assembler = RecordingAssembler()
    core, repository = synthetic_core(FailingChatModel(), context_assembler=assembler)
    with pytest.raises(AdapterUnavailableError):
        core.respond_to_text("현재 질문")
    assert len(assembler.inputs) == 1
    assert repository.list_messages()[-1] == ConversationMessage("user", "현재 질문")
    assert len(repository.list_messages()) == 3
