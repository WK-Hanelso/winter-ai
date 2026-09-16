"""P3-3 prompt contract; the pre-extraction fixture remains as historical evidence."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import random

import pytest

from companion.adapters.fake import FailingChatModel, InMemoryConversationRepository
from companion.beliefs import Belief
from companion.capabilities import OPERATION_DESCRIPTION, SelfCapability, SelfModelSnapshot
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
FIXTURE = Path(__file__).parents[1] / "fixtures/text-context-p05.json"
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


def capture(case, *, companion_self_model=False):
    model = SpyModel()
    core, repository = synthetic_core(model, full=case in ("full", "explain", "streaming"),
                                      bounded=case != "minimal", context_assembler=ContextAssembler(
                                          companion_self_model=companion_self_model))
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
def test_requests_match_p05_baseline(case):
    baseline = json.loads(FIXTURE.read_text())
    requests = capture(case)
    # P8 changes only the outside-world rule. Restore its exact previous wording
    # for the historical hashes; independently assert the new rule below.
    from companion.grounding import NO_INVENTED_EXPERIENCE

    previous = (
        "바깥일은 네가 볼 수 없어. 날씨, 뉴스, 천우가 오늘 뭘 했는지는 "
        "지어내지 말고 물어봐. 대신 네 생각이나 기분은 네 것이니 말해도 돼."
    )
    assert all(any(NO_INVENTED_EXPERIENCE in m.content for m in r.messages) for r in requests)
    requests = [replace(r, messages=tuple(replace(m, content=m.content.replace(
        NO_INVENTED_EXPERIENCE, previous)) for m in r.messages)) for r in requests]
    if case in ("full", "explain", "streaming"):
        # P3-1b/P5.2 change memory rendering. Restore the historical block
        # for the hash, checking all other bytes and order unchanged.
        current = (
            "아래는 네가 이미 아는 천우의 배경이야. 물어보면 지금 대화에 맞는 한두 가지만 "
            "골라 네 말로 짧게 말하고, 목록처럼 읊거나 전부 말하지 마. "
            "안 물어보면 굳이 꺼내지 마. "
            "괄호 날짜는 그 말을 한 때다. 오래된 상태는 지금과 다를 수 있으니 "
            "단정하지 말고 필요하면 물어봐.\nPython config를 선호해."
        )
        old = (
            "천우가 직접 말해 활성화된 관련 기억이야. "
            "current_state:*는 천우의 영구 특성이 아니라 지금의 상태이며, "
            "같은 주제의 새 상태가 들어오면 최신 값으로 교체돼:\n"
            "- [memory-1 | preference] Python config를 선호해"
        )
        for request in requests:
            assert sum(m.content == current for m in request.messages) == 1
            # P6 appends exactly one separately snapshot-tested operation block.
            # Remove only that suffix for the historical hash; retain the old fixture.
            assert sum(m.content.endswith('\n' + OPERATION_DESCRIPTION)
                       for m in request.messages) == 1
        requests = [replace(request, messages=tuple(
            replace(m, content=old) if m.content == current else
            replace(m, content=m.content.removesuffix('\n' + OPERATION_DESCRIPTION))
            for m in request.messages
        )) for request in requests]
    # Reverse only the authorized P3-3 ordering/split, preserving historical hashes.
    # Explicit order assertions below independently guard the new wire contract.
    normalized = []
    for request in requests:
        m = request.messages
        if case in ("full", "explain", "streaming"):
            joined = replace(m[2], content=m[2].content + "\n\n" + m[7].content)
            ordered = (m[0], m[1], joined, m[3], m[4], m[11], m[5], m[9],
                       m[10], m[8], m[12], m[6], m[13])
        elif case == "minimal":
            joined = replace(m[1], content=m[1].content + "\n\n" + m[2].content)
            ordered = (m[0], joined, m[3])
        else:
            joined = replace(m[1], content=m[1].content + "\n\n" + m[3].content)
            ordered = (m[0], joined, m[2], *m[4:])
        normalized.append(replace(request, messages=ordered))
    requests = normalized
    actual = [hashlib.sha256(json.dumps(asdict(request), ensure_ascii=False,
                                      sort_keys=True).encode()).hexdigest()
              for request in requests]
    assert actual == baseline[case]


def test_full_request_preserves_system_block_order_and_evidence():
    request = capture("full")[0]
    blocks = [message.content for message in request.messages if message.role == "system"]
    assert len(blocks) == 12
    markers = ["너는 겨울이야", "문장 이내로", "지어내지 마", "SelfModel", "개선 안건",
               "아래는 네가 이미 아는 천우의 배경", "질문에 먼저 직접 답해",
               "예전에 설정을 비교했어", "예전에는 YAML을 썼어", "belief-1",
               "지난날 회고", "loop-1"]
    for block, marker in zip(blocks, markers, strict=True):
        assert marker in block
    assert request.messages[6] == ConversationMessage("assistant", "이전 답변")
    assert request.messages[-1] == ConversationMessage("user", "설정 이야기를 해볼까?")


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


def test_companion_prefix_is_byte_identical_across_changed_turn_evidence():
    from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime

    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"),
                                 chat_model=SpyModel())
    try:
        examples = runtime.core._context_assembler._examples
    finally:
        runtime.close()
    assembler = RecordingAssembler()
    core, _ = synthetic_core(SpyModel(), full=True, context_assembler=assembler)
    core._prepare("첫 질문")
    first = assembler.inputs[0]
    # Use the real companion examples, including corrections and the boundary,
    # with both style and grounding/dialogue instruction slots populated.
    cached = ContextAssembler(examples=examples)
    one = cached.assemble(**first)
    second = dict(first, text="둘째 질문",
                  memories=(replace(first["memories"][0], content="이제 TOML을 선호해"),),
                  recent_messages=(ConversationMessage("user", "둘째 질문"),),
                  snapshot=replace(first["snapshot"], self_model=None, improvements=None),
                  recalled_conversation=(ConversationMessage("user", "새 회상"),))
    two = cached.assemble(**second)
    count = 1 + len(examples) + 2  # identity, examples/boundary, style, fixed grounding
    expected = (ConversationMessage("system", first["snapshot"].identity.system_message()),)
    expected += examples
    expected += (ConversationMessage("system", first["style_instruction"]),
                 ConversationMessage("system", first["grounding_instruction"]),)
    assert one.messages[:count] == expected
    def encode(messages):
        return json.dumps([asdict(message) for message in messages],
                          ensure_ascii=False).encode("utf-8")
    assert encode(one.messages[:count]) == encode(two.messages[:count])
    assert one.messages[count:] != two.messages[count:]
    assert cached.assemble(**second) == two
