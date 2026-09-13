"""P3-1c: bounded regeneration before persistence/delivery, with original receipts."""

from difflib import SequenceMatcher
import json
from pathlib import Path
import sqlite3

import pytest
from test_schedule_dialogue import Harness, output

from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime
from companion.contracts import ChatResult, ConversationMessage
from companion.dialogue.repeated_reply import REGENERATION_INSTRUCTION, is_repeated_reply

REPEATED = "오늘 이야기는 천천히 해도 괜찮아. 어떤 이야기부터 꺼내고 싶어?"
FRESH = "같은 말을 또 했네. 아까 고른 영화는 제목부터 좀 수상한데ㅋㅋ"


class Model:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ChatResult(next(self.replies))


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    assert harness.runtime.close()


def seed(repository, *replies):
    for reply in replies:
        repository.append(ConversationMessage("user", "이야기하자"))
        repository.append(ConversationMessage("assistant", reply))


def repeated_observations(path):
    return [row for line in (path / "claim_observations.jsonl").read_text().splitlines()
            if (row := json.loads(line))["stage"] == "repeated_reply"]


@pytest.mark.parametrize("streaming", [False, True])
def test_repeat_regenerates_once_before_saving_or_displaying(h, streaming):
    # First answer is delivered normally; the next draft copies it verbatim.
    first, _ = h.turn("오늘 이야기하자", draft=REPEATED)
    assert first == REPEATED
    h.model.queue.extend([output(REPEATED), output(FRESH)])
    emitted = []
    core = h.runtime.core
    response = (core.respond_to_text_streaming("방금 한 말이랑 똑같잖아", emitted.append)
                if streaming else core.respond_to_text("방금 한 말이랑 똑같잖아"))
    assert response.text == FRESH
    assert len(h.model.requests) == 3
    assert h.observations()[-1]["calls"] == 2
    assert ConversationMessage("system", REGENERATION_INSTRUCTION) in h.model.requests[-1].messages
    assert h.model.requests[-1].response_format == h.model.requests[-2].response_format
    assert [m.content for m in h.runtime.resources.repository.list_messages()
            if m.role == "assistant"] == [REPEATED, FRESH]
    if streaming:
        assert " ".join(emitted) == FRESH
    assert not repeated_observations(h.path)


@pytest.mark.parametrize("exhausted", [False, True])
def test_repeat_with_no_remaining_call_keeps_text_and_logs(h, exhausted):
    seed(h.runtime.resources.repository, REPEATED)
    h.model.queue.extend(["invalid JSON" if exhausted else output(REPEATED), output(REPEATED)])
    response = h.runtime.core.respond_to_text("다른 이야기를 해줘")
    assert response.text == REPEATED
    assert len(h.model.requests) == h.observations()[-1]["calls"] == 2
    assert h.runtime.resources.repository.list_messages()[-1].content == REPEATED
    observation, = repeated_observations(h.path)
    assert observation["text"] == REPEATED
    assert observation["violations"] == [
        {"kind": "repeated_reply", "sentence": REPEATED, "action": "observe"},
    ]
    assert any(REGENERATION_INSTRUCTION == m.content
               for m in h.model.requests[-1].messages) is not exhausted


def test_repeated_regeneration_adopts_second_text_without_template(h):
    second = REPEATED + " 천천히."
    seed(h.runtime.resources.repository, REPEATED)
    h.model.queue.extend([output(REPEATED), output(second)])
    assert h.runtime.core.respond_to_text("또 똑같네").text == second
    observation, = repeated_observations(h.path)
    assert observation["text"] == second
    assert len(h.model.requests) == 2


def test_short_fixed_reply_is_excluded(tmp_path):
    model = Model()
    runtime = build_text_runtime(TextRuntimeConfig(
        dialogue_policy="companion", memory_db=tmp_path / "memories.sqlite",
    ), chat_model=model)
    try:
        seed(runtime.resources.repository, "기억해둘게.")
        assert runtime.core.respond_to_text("나는 파란색을 좋아해. 기억해").text == "기억해둘게."
        assert not model.requests
    finally:
        assert runtime.close()


def test_receipt_is_preserved_and_schedule_executes_only_once(h, monkeypatch):
    old = "내일 독서 모임을 로컬 일정에 저장했어. 이제 일정에서 확인할 수 있어."
    new = "독서 모임은 내일이야. 책 얘기하다 보면 시간이 금방 가겠네."
    seed(h.runtime.resources.repository, old)
    receipts = []
    execute = h.dialogue.execute

    def counted_execute(state, source):
        receipt = execute(state, source)
        receipts.append(receipt)
        return receipt

    monkeypatch.setattr(h.dialogue, "execute", counted_execute)
    h.model.queue.extend([
        output(old, "create", dict(title="독서 모임", date="내일")), output(new),
    ])
    assert h.runtime.core.respond_to_text("내일 독서 모임 일정 저장해줘").text == new
    receipt, = receipts
    record = h.observations()[-1]
    assert record["calls"] == 2 and record["receipt"]["command_id"] == receipt.command_id
    assert record["receipt"]["status"] == "applied"
    assert h.count_history() == 1 and len(h.repo.query()) == 1
    block = next(m for m in h.model.requests[-1].messages if m.content.startswith("확인된 일정"))
    assert json.loads(block.content.split("\n", 1)[1])["receipt"]["status"] == "applied"
    assert record["proposal"]["operation"] == "create"


def test_receipt_followup_exhausts_budget_before_repeat_guard(h):
    final = "내일 독서 모임을 로컬 일정에 저장했어. 이제 일정에서 확인할 수 있어."
    seed(h.runtime.resources.repository, final)
    h.model.queue.extend([
        output("금요일 독서 모임 저장했어.", "create", dict(title="독서 모임", date="내일")),
        output(final),
    ])
    assert h.runtime.core.respond_to_text("내일 독서 모임 일정 저장해줘").text == final
    assert len(h.model.requests) == 2 and h.count_history() == 1
    assert h.observations()[-1]["receipt"]["status"] == "applied"
    assert len(repeated_observations(h.path)) == 1


@pytest.mark.parametrize("retry", [
    "invalid JSON",
    output(FRESH, "create", dict(title="두 번째 일정", date="내일")),
])
def test_invalid_retry_keeps_first_answer_without_executing_a_new_proposal(h, retry):
    seed(h.runtime.resources.repository, REPEATED)
    h.model.queue.extend([output(REPEATED), retry])
    assert h.runtime.core.respond_to_text("똑같은 말을 또 하네").text == REPEATED
    assert len(h.model.requests) == 2 and h.count_history() == 0
    assert h.observations()[-1]["receipt"] is None
    assert h.observations()[-1]["proposal"] is None
    assert len(repeated_observations(h.path)) == 1


@pytest.mark.parametrize("policy,expected,calls", [
    ("companion", FRESH, 2), ("legacy", REPEATED, 1),
])
def test_plain_path_uses_same_guard_and_legacy_is_unchanged(policy, expected, calls):
    model = Model(REPEATED, FRESH)
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy=policy), chat_model=model)
    try:
        seed(runtime.resources.repository, REPEATED)
        assert runtime.core.respond_to_text("다른 이야기도 해줘").text == expected
        assert len(model.requests) == calls
        assert all(r.response_format is None for r in model.requests)
    finally:
        assert runtime.close()


def test_plain_repair_consumes_the_same_two_call_budget():
    model = Model("처음 초안", REPEATED)
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    observations = []

    class Reviewer:
        def repair_instruction(self, behavior, draft, **kwargs):
            return "다시 써" if draft == "처음 초안" else None

    runtime.core._response_reviewer = Reviewer()
    runtime.core._claim_observation_recorder = observations.append
    try:
        seed(runtime.resources.repository, REPEATED)
        assert runtime.core.respond_to_text("이야기해줘").text == REPEATED
        assert len(model.requests) == 2
        assert any(o.stage == "repeated_reply" for o in observations)
    finally:
        assert runtime.close()


def test_plain_stream_does_not_deliver_or_persist_the_repeated_draft():
    class StreamingModel(Model):
        def generate_stream(self, request):
            text = self.generate(request).text
            yield text[:10]
            yield text[10:]

    model = StreamingModel(REPEATED, FRESH)
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    emitted = []
    try:
        seed(runtime.resources.repository, REPEATED)
        response = runtime.core.respond_to_text_streaming("또 똑같은 말이네", emitted.append)
        assert response.text == " ".join(emitted) == FRESH
        assert len(model.requests) == 2
        assert runtime.resources.repository.list_messages()[-1].content == FRESH
    finally:
        assert runtime.close()


@pytest.mark.parametrize("position", [1, 3, 6, 7])
def test_only_last_six_actual_assistant_replies_count(h, position):
    seed(h.runtime.resources.repository, REPEATED, *("짧은 답" for _ in range(position - 1)))
    # Several unanswered turns and a small prompt window must not hide past replies.
    for _ in range(10):
        h.runtime.resources.repository.append(ConversationMessage("user", "대답해줘"))
    h.model.queue.extend([output(REPEATED), output(FRESH)])
    final = h.runtime.core.respond_to_text("다른 이야기도 해줘").text
    assert final == (FRESH if position <= 6 else REPEATED)
    assert len(h.model.requests) == (2 if position <= 6 else 1)


def test_fictional_examples_do_not_trigger_regeneration():
    from configs.dialogue.companion_examples import EXAMPLES

    example = next(reply for _, reply in EXAMPLES if len(reply) >= 20)
    model = Model(example)
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    try:
        assert runtime.core.respond_to_text("이야기해줘").text == example
        assert len(model.requests) == 1
    finally:
        assert runtime.close()


def test_similarity_boundaries():
    assert not is_repeated_reply("가" * 19, ["가" * 19])
    assert is_repeated_reply("가" * 20, ["가" * 20])
    assert is_repeated_reply("가" * 20, ["가" * 19])
    assert is_repeated_reply("가" * 15 + "나" * 5, ["가" * 20])  # Exactly 0.75.
    assert not is_repeated_reply("가" * 14 + "나" * 6, ["가" * 20])
    assert not is_repeated_reply(REPEATED, [REPEATED] + ["짧아"] * 6)
    assert is_repeated_reply(REPEATED, [REPEATED] + ["짧아"] * 5)


def test_private_original_500_502_504_and_512_read_only():
    # Personal text stays in its original DB; never copy it into a tracked fixture.
    path = Path(__file__).resolve().parents[2] / "data/conversations.sqlite"
    if not path.is_file():
        pytest.skip("Local private replay requires the original conversation DB")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        rows = dict(connection.execute(
            "SELECT id, content FROM conversation_messages "
            "WHERE role = 'assistant' AND id IN (500, 502, 504, 512)"
        ))
    if len(rows) != 4:
        pytest.skip("Local private replay rows are absent")
    assert is_repeated_reply(rows[500], [rows[500]])
    assert is_repeated_reply(rows[504], [rows[500], rows[502]])
    assert is_repeated_reply(rows[512], [rows[500]])
    assert SequenceMatcher(None, rows[502], rows[504]).ratio() == pytest.approx(0.8089330025)
    assert not is_repeated_reply(rows[502], [rows[500]])
