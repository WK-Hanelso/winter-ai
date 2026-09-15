"""P3-3 prefix stability and timings, using synthetic data and temporary SQLite."""

from dataclasses import replace
from datetime import datetime
import json

import pytest
from test_llama_cpp_adapter import FakeHttpResponse
from test_memory_inclusion import FixedClock, Store, memories
from test_schedule_dialogue import Harness, output

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ChatResult, ChatTimings, ConversationMessage
from companion.memory import ActiveMemoryRetriever, memory_context
from companion.model_priority import ForegroundPriorityChatScheduler
from companion.schedules import KST


def rows(n, width=1):
    return tuple(ConversationMessage("user" if i % 2 == 0 else "assistant",
                                     f"{i:03}" + "x" * width) for i in range(n))


def test_message_watermarks_and_bounded_repository_fetch():
    builder = ConversationContextBuilder()
    assert builder.message_limit == 21  # high watermark plus overflow sentinel
    history = rows(35)
    for count in range(1, 21):
        assert builder.build(history[:count][-builder.message_limit:]) == history[:count]
    assert builder.build(history[:21][-builder.message_limit:]) == history[9:21]
    for count in range(22, 30):
        assert builder.build(history[:count][-builder.message_limit:]) == history[9:count]
    assert builder.build(history[:30][-builder.message_limit:]) == history[18:30]


def test_character_watermarks_restart_and_current_oversize():
    builder = ConversationContextBuilder(max_characters=12, high_characters=20)
    history = rows(8)  # four chars per row
    for count in range(1, 6):
        assert builder.build(history[:count]) == history[:count]
    assert builder.build(history[:6]) == history[3:6]
    assert builder.build(history[:7]) == history[3:7]
    assert ConversationContextBuilder(max_characters=12).build(history) == history[-3:]
    huge = ConversationMessage("user", "x" * 7000)
    assert builder.build(history + (huge,)) == (huge,)
    assert builder.build(()) == ()
    assert builder.build(history[:1]) == history[:1]


def test_changed_or_excluded_history_does_not_resurrect_cached_rows():
    builder = ConversationContextBuilder()
    history = rows(15)
    builder.build(history[:10])
    changed = history[:3] + history[4:11]
    assert builder.build(changed) == changed
    edited = (replace(changed[0], content="edited"),) + changed[1:]
    assert builder.build(edited) == edited


def test_kind_created_id_order_and_small_store_all_inclusion():
    kinds = ("current_state:work", "episodic", "procedural", "decision",
             "preference", "project", "relationship", "semantic")
    items = memories(tuple((kind, f"fact-{i}") for i, kind in enumerate(kinds)))
    extra = replace(items[-1], id="z", content="last semantic")
    items += (extra,)
    retriever = ActiveMemoryRetriever(Store(items), max_memories=1)
    one = retriever.retrieve("fact-0 기억나?")
    two = retriever.retrieve("fact-7 오늘은 어때?")
    assert set(one) == set(two) == set(items)
    rendered = memory_context(one, clock=FixedClock())
    assert rendered == memory_context(tuple(reversed(two)), clock=FixedClock())
    expected = (items[7], extra, *reversed(items[:7]))
    positions = [rendered.index(m.content) for m in expected]
    assert positions == sorted(positions)


def test_over_budget_selection_keeps_p5_but_render_order_is_fixed():
    items = memories((("semantic", "프로필"), ("episodic", "Python 경험"),
                      ("decision", "Python 결정"), ("project", "무관 " * 1000)))
    retriever = ActiveMemoryRetriever(Store(items), max_characters=30)
    selected = retriever.retrieve("Python")
    assert set(selected) == set(items[:3])
    rendered = memory_context(selected, clock=FixedClock())
    assert rendered == memory_context(tuple(reversed(selected)), clock=FixedClock())
    assert rendered.index("프로필") < rendered.index("Python 결정") < rendered.index("Python 경험")


def test_real_runtime_two_turn_prefix_and_current_last(tmp_path):
    h = Harness(tmp_path)
    try:
        core = h.runtime.core
        items = memories((("semantic", "프로필"), ("episodic", "전시회 경험")))
        core._memory_retriever = ActiveMemoryRetriever(Store(items))
        core._clock = FixedClock()
        repo = h.runtime.resources.repository
        for message in rows(11):
            repo.append(message)
        first = core._prepare("전에 무슨 이야기를 했지?").request
        repo.append(ConversationMessage("assistant", "전시회 이야기였어."))
        h.clock.value = h.clock.value.replace(minute=59, second=59, microsecond=123)
        second = core._prepare("오늘은 어때?").request
        memory_at = next(i for i, m in enumerate(first.messages)
                         if m.content.startswith("아래는 네가 이미 아는"))
        prefix_end = memory_at + 1 + 11
        assert first.messages[:prefix_end] == second.messages[:prefix_end]
        assert first.messages[memory_at + 1:prefix_end] == rows(11)
        assert second.messages[prefix_end:prefix_end + 2] == (
            first.messages[-1], ConversationMessage("assistant", "전시회 이야기였어."))
        assert first.messages[-1] == ConversationMessage("user", first.prompt)
        assert second.messages[-1] == ConversationMessage("user", second.prompt)
        for request in (first, second):
            date_at = next(i for i, m in enumerate(request.messages)
                           if m.content.startswith("일정 날짜 기준"))
            assert date_at > prefix_end
        def date(request):
            return next(m for m in request.messages if m.content.startswith("일정 날짜 기준"))

        assert date(first) == date(second)
        # Companion currently uses one act instruction; it still stays in the tail.
        assert first.messages[prefix_end].role == "system"
    finally:
        h.runtime.close()


def test_hour_precision_and_kst_conversion(tmp_path):
    h = Harness(tmp_path)
    try:
        turn = h.dialogue.begin(1)
        stamp = datetime(2026, 9, 15, 10, 29, 52, 848081, tzinfo=KST)
        first = h.dialogue.variable_messages(replace(turn, received_at=stamp))[0]
        assert first.content == "일정 날짜 기준(KST): 2026-09-15 (화) 10시"
        assert h.dialogue.variable_messages(replace(
            turn, received_at=stamp.replace(minute=59, second=59)))[0] == first
        assert h.dialogue.variable_messages(replace(
            turn, received_at=stamp.replace(hour=11)))[0] != first
    finally:
        h.runtime.close()


TIMINGS = ChatTimings(321, 4000, 1200.5, 300.25)
VALUES = dict(prompt_n=321, cache_n=4000, prompt_ms=1200.5, predicted_ms=300.25)
KEYS = ("prompt_tokens", "cached_tokens", "prompt_ms", "predicted_ms")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("measured", [False, True])
def test_adapter_timings_reach_core_observation(
    tmp_path, monkeypatch, streaming, scheduled, measured,
):
    h = Harness(tmp_path)
    payload = output("응, 듣고 있어.")
    if streaming:
        chunks = [dict(choices=[dict(delta=dict(content=payload))]),
                  dict(choices=[dict(delta={}, finish_reason="stop")])]
        if measured:
            # timings-only final event, after the finish event
            chunks.append(dict(choices=[], timings=VALUES))
        body = ("".join("data: " + json.dumps(c) + "\n" for c in chunks)
                + "data: [DONE]\n").encode()
    else:
        response = dict(choices=[dict(message=dict(content=payload), finish_reason="stop")])
        if measured:
            response["timings"] = VALUES
        body = json.dumps(response).encode()
    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen",
                        lambda *a, **k: FakeHttpResponse(body))
    adapter = LlamaCppHttpChatModel("http://synthetic.invalid")
    if streaming:
        model = adapter
    else:
        class NonStreaming:
            def generate(self, request):
                return adapter.generate(request)
        model = NonStreaming()
    if scheduled:
        model = ForegroundPriorityChatScheduler(model).foreground()
    h.runtime.core._chat_model = model
    try:
        h.runtime.core.respond_to_text("안녕")
        record = h.observations()[-1]
        expected = [321, 4000, 1200.5, 300.25] if measured else [None] * 4
        assert [record[k] for k in KEYS] == expected
    finally:
        h.runtime.close()


def test_fake_without_timings_and_retry_preserves_first_measurement(tmp_path):
    h = Harness(tmp_path)
    try:
        _, record = h.turn("안녕", draft="반가워.")
        assert [record[k] for k in KEYS] == [None] * 4
        class Model:
            calls = 0

            def generate(self, request):
                self.calls += 1
                if self.calls == 1:
                    return ChatResult("invalid JSON", TIMINGS)
                return ChatResult(output("잘 들었어."), ChatTimings(1, 2, 3, 4))
        h.runtime.core._chat_model = Model()
        h.runtime.core.respond_to_text("이야기하자")
        record = h.observations()[-1]
        assert record["calls"] == 2
        assert [record[k] for k in KEYS] == [321, 4000, 1200.5, 300.25]
    finally:
        h.runtime.close()


def test_stream_timings_are_per_call_and_absent_values_do_not_leak(monkeypatch):
    bodies = iter([b'data: {"choices": [], "timings": {"prompt_n": 3}}\n',
                   b'data: [DONE]\n'])
    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen",
                        lambda *a, **k: FakeHttpResponse(next(bodies)))
    model = LlamaCppHttpChatModel("http://synthetic.invalid")
    for expected in (ChatTimings(prompt_n=3), None):
        with pytest.raises(StopIteration) as stop:
            next(model.generate_stream(ChatRequest("hi")))
        assert stop.value.value == expected


def test_malformed_optional_timings_do_not_break_valid_text(monkeypatch):
    response = dict(choices=[dict(message=dict(content="ok"))],
                    timings=dict(prompt_n=True, cache_n=-1, prompt_ms="no",
                                 predicted_ms=float("nan")))
    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen",
                        lambda *a, **k: FakeHttpResponse(json.dumps(response).encode()))
    result = LlamaCppHttpChatModel("http://synthetic.invalid").generate(ChatRequest("hi"))
    assert result == ChatResult("ok", ChatTimings())


def test_exact_assembly_order_and_changed_act_tail():
    from test_context_assembly_baseline import RecordingAssembler, SpyModel, synthetic_core

    from companion.dialogue.context_assembler import ContextAssembler

    capture = RecordingAssembler()
    core, _ = synthetic_core(SpyModel(), full=True, context_assembler=capture)
    core._prepare("설정 이야기를 해볼까?")
    inputs = capture.inputs[0]
    examples = (ConversationMessage("user", "예시 질문"),
                ConversationMessage("assistant", "예시 응답"),
                ConversationMessage("system", "실제 대화 규칙"))
    assembler = ContextAssembler(examples)
    args = dict(inputs, dialogue_instruction="회상 지시", schedule_instruction="일정 JSON",
                schedule_context=(ConversationMessage("system", "일정 날짜 기준"),))
    one = assembler.assemble(**args)
    contents = [m.content for m in one.messages]
    markers = ["너는 겨울이야", "예시 질문", "예시 응답", "실제 대화 규칙", "문장 이내로",
               "지어내지 마", "일정 JSON", "SelfModel", "개선 안건",
               "아래는 네가 이미 아는", "이전 답변", "회상 지시",
               "예전에 설정을 비교했어", "예전에는 YAML을 썼어", "belief-1",
               "지난날 회고", "loop-1", "일정 날짜 기준", "설정 이야기를 해볼까?"]
    assert len(contents) == len(markers)
    assert all(marker in content for marker, content in zip(markers, contents, strict=True))
    assert [m.role for m in one.messages] == [
        "system", "user", "assistant", *(["system"] * 7), "assistant",
        *(["system"] * 7), "user",
    ]
    two = assembler.assemble(**dict(args, text="오늘은 어때?", dialogue_instruction="일상 지시",
        recent_messages=inputs["recent_messages"] + (
            ConversationMessage("assistant", "응, 이야기하자."),
            ConversationMessage("user", "오늘은 어때?"))))
    assert one.messages[:11] == two.messages[:11]
    assert two.messages[11:13] == (one.messages[-1],
                                  ConversationMessage("assistant", "응, 이야기하자."))
    assert two.messages[13].content == "일상 지시"
    assert two.messages[-1].content == "오늘은 어때?"


def test_created_at_precedes_id_in_render_order():
    base = memories((("semantic", "최신 사실"),))[0]
    old = replace(base, id="z", content="오래된 사실", created_at="2026-09-12T00:00:00+00:00")
    rendered = memory_context((base, old), clock=FixedClock())
    assert rendered.index(old.content) < rendered.index(base.content)


def test_state_history_query_keeps_active_memory_prefix(tmp_path):
    from companion.memory import SqliteMemoryRepository

    h = Harness(tmp_path)
    try:
        repository = SqliteMemoryRepository(tmp_path / "synthetic-memory.sqlite")
        repository.remember_current_state(topic="wellbeing", content="예전에는 피곤했어.")
        current = repository.remember_current_state(topic="wellbeing", content="지금은 괜찮아.")
        core = h.runtime.core
        core._memory_retriever = ActiveMemoryRetriever(repository)
        first = core._prepare("안녕").request
        second = core._prepare("예전과 지금 내 마음 상태가 어떻게 달라?").request
        first_memory = next(m for m in first.messages if m.content.startswith("아래는 네가"))
        second_memory = next(m for m in second.messages if m.content.startswith("아래는 네가"))
        assert first_memory == second_memory
        assert current.content in first_memory.content
        assert any("현재 상태 Timeline" in m.content for m in second.messages)
    finally:
        h.runtime.close()
