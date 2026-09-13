"""M1 inclusion checks; synthetic stores and fake responses, no personal DB."""

from dataclasses import replace

import pytest

from companion.adapters.fake import InMemoryConversationRepository
from companion.contracts import ChatResult
from companion.core import CompanionCore
from companion.memory import ActiveMemoryRetriever, Memory, memory_context

STAMP = "2026-09-13T00:00:00+00:00"
# Same shape as the reported ten-memory store; these are synthetic facts/questions.
FACTS = (
    ("semantic", "1990년생, AI 모델 선행팀 선임연구원"),
    ("semantic", "예시 연구소에 다닌다"),
    ("project", "VLM E2E 연구"),
    ("relationship", "겨울이와 오래 대화하고 싶다"),
    ("preference", "간결한 설명을 좋아한다"),
    ("decision", "계정 위임은 건별로 승인한다"),
    ("project", "로컬 대화 프로그램을 개발한다"),
    ("semantic", "이름은 테스트 사용자다"),
    ("current_state:wellbeing", "오늘 조금 피곤하다"),
    ("current_state:workload", "요즘 업무가 많다"),
)
QUESTIONS = ("내가 뭐하는 사람인지 알아?", "나에 대해 뭐 알아?", "내 직업이 뭔지 알아?")


def memories(facts=FACTS):
    return tuple(Memory(str(i), kind, content, 3, 1.0, "active", "explicit",
                        STAMP, STAMP, None, None, "synthetic", "durable")
                 for i, (kind, content) in enumerate(facts))


class Store:
    def __init__(self, items):
        self.items = items

    def list_active(self):
        return self.items


class Capture:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ChatResult("응, 듣고 있어.")


@pytest.mark.parametrize("question", QUESTIONS + ("안녕", "내가뭐하는사람인지알아"))
def test_small_store_includes_all_ten_in_real_core_prompt(question):
    items = memories()
    model = Capture()
    core = CompanionCore(model, InMemoryConversationRepository(),
                         memory_retriever=ActiveMemoryRetriever(Store(items)))
    core.respond_to_text(question)
    blocks = [m.content for m in model.requests[0].messages if m.role == "system"]
    block = next(b for b in blocks if "활성화된 관련 기억" in b)
    assert all(memory.content in block for memory in items)
    assert "[사실]" in block and "[현재 상태]" in block
    assert all(memory.id not in block.splitlines() for memory in items)


def overflow():
    # 20 items exceed 1500 chars: four profile kinds, twelve decisions, four states.
    facts = [(kind, f"프로필{i} " + "배경 " * 30)
             for i, kind in enumerate(("semantic", "relationship", "project", "preference"))]
    facts += [("decision", f"키워드 일치{i} " + "계정 " * 30) for i in range(12)]
    facts += [(f"current_state:topic{i}", f"현재{i} " + "상태 " * 30) for i in range(4)]
    return memories(facts)


@pytest.mark.parametrize("question", QUESTIONS + ("나는 누구?", "직업이 뭐야"))
def test_overflow_prefers_profile_kinds_and_states_have_separate_slots(question):
    items = overflow()
    assert len(items) == 20 and sum(len(m.content) for m in items) > 1500
    retrieved = ActiveMemoryRetriever(Store(items), max_memories=4).retrieve(question + " 계정?")
    assert {m.kind for m in retrieved[:4]} == {
        "semantic", "relationship", "project", "preference",
    }
    assert len(retrieved) == 6
    assert sum(m.kind.startswith("current_state:") for m in retrieved) == 2
    default = ActiveMemoryRetriever(Store(items)).retrieve(question)
    assert len(default) == 5
    assert all(m.kind != "decision" for m in default)
    assert sum(len(m.content) for m in default) <= 1500


def test_overflow_keeps_keyword_ranking_and_does_not_boost_states():
    items = list(overflow())
    items[4] = replace(items[4], content="키워드 일치 계정 Python config", importance=9)
    retrieved = ActiveMemoryRetriever(Store(items)).retrieve("Python config 계정")
    assert retrieved[0] == items[4]
    assert len(retrieved) == 5
    assert all(m.kind == "decision" for m in retrieved[:3])
    assert all(m.kind.startswith("current_state:") for m in retrieved[3:])
    # '내일' must not become a self-profile question just because it contains '내'.
    unrelated = ActiveMemoryRetriever(Store(items)).retrieve("내일 날씨 어때?")
    assert len(unrelated) == 2
    assert all(m.kind.startswith("current_state:") for m in unrelated)


def test_exact_budget_includes_everything_and_overflow_never_truncates_sources():
    items = memories()
    size = sum(len(m.content) for m in items)
    assert ActiveMemoryRetriever(Store(items), max_characters=size).retrieve("안녕") == items
    assert ActiveMemoryRetriever(Store(items), max_characters=size - 1).retrieve("안녕") == (
        items[9], items[8],
    )
    long = replace(items[0], content="초장문 " * 400)
    selected = ActiveMemoryRetriever(Store((long,) + items[1:]), max_characters=60).retrieve(
        "나에 대해 뭐 알아?"
    )
    assert long not in selected
    assert sum(len(m.content) for m in selected) <= 60
    assert all(m in items for m in selected)


def test_grouped_block_preserves_exact_text_and_unknown_kind():
    items = memories((("semantic", "  원문\n줄바꿈  "), ("project", "다른 종류"),
                      ("semantic", "두 번째 사실"), ("custom", "알 수 없는 종류")))
    block = memory_context(items)
    assert block.count("[사실]") == 1
    assert "[사실]\n-   원문\n줄바꿈  \n- 두 번째 사실\n[프로젝트]" in block
    assert "[custom]\n- 알 수 없는 종류" in block


def test_small_store_all_inclusion_also_applies_to_current_states():
    items = memories(tuple((f"current_state:{i}", f"상태 {i}") for i in range(4)))
    assert ActiveMemoryRetriever(Store(items)).retrieve("안녕") == items


def test_empty_and_zero_budget():
    assert ActiveMemoryRetriever(Store(())).retrieve("나에 대해?") == ()
    assert ActiveMemoryRetriever(Store(memories()), max_characters=0).retrieve("나에 대해?") == ()
