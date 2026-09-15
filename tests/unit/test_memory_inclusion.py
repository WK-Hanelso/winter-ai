"""P5 selection and inherited prose checks; synthetic stores and fake responses."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from companion.adapters.fake import InMemoryConversationRepository
from companion.contracts import ChatResult
from companion.core import CompanionCore
from companion.memory import (
    MEMORY_BACKGROUND_INSTRUCTION,
    ActiveMemoryRetriever,
    Memory,
    memory_context,
)

STAMP = "2026-09-13T00:00:00+00:00"


class FixedClock:
    def now(self):
        return datetime(2026, 9, 14, tzinfo=UTC)


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
def test_small_store_profiles_and_fallback_in_real_core_prompt(question):
    items = memories()
    model = Capture()
    core = CompanionCore(model, InMemoryConversationRepository(),
                         clock=FixedClock(),
                         memory_retriever=ActiveMemoryRetriever(Store(items)))
    core.respond_to_text(question)
    blocks = [m.content for m in model.requests[0].messages if m.role == "system"]
    block = next(b for b in blocks if b.startswith(MEMORY_BACKGROUND_INSTRUCTION))
    assert all(m.content in block for m in items if m.kind in {
        "semantic", "relationship", "preference", "project",
    })
    assert all(m.content in block for m in items)  # P3-3 restores all small-store facts
    instruction, paragraph = block.split("\n", 1)
    assert instruction == MEMORY_BACKGROUND_INSTRUCTION
    assert "[" not in paragraph and "\n" not in paragraph
    assert not paragraph.startswith("- ")
    assert all(memory.id not in block.splitlines() for memory in items)


def test_overlap_confidence_recency_rank_after_profiles():
    items = list(memories((("semantic", "기본 프로필"), ("relationship", "관계"),
                           ("decision", "Python config 결정"),
                           ("procedural", "Python config 설명"),
                           ("episodic", "Python 경험"),
                           ("episodic", "전시회 경험"))))
    items[5] = replace(items[5], content="무관 " * 1000)
    items[2] = replace(items[2], confidence=.8, created_at="2026-09-14")
    items[3] = replace(items[3], confidence=.9, created_at="2026-09-13")
    selected = ActiveMemoryRetriever(Store(items)).retrieve("Python config")
    assert {m.kind for m in selected[:2]} == {"semantic", "relationship"}
    assert selected[2:] == (items[3], items[2], items[4])
    items[2] = replace(items[2], confidence=.9)
    selected = ActiveMemoryRetriever(Store(items)).retrieve("Python config")
    assert selected[2:] == (items[2], items[3], items[4])


def test_fallback_latest_three_and_recall_all_within_budget():
    items = list(memories(tuple(("preference" if i % 2 else "project", f"관심사 {i}")
                                for i in range(8))))
    items = [replace(m, created_at=f"2026-09-{i + 1:02}", confidence=1 - i / 20)
             for i, m in enumerate(items)]
    overflow = replace(items[0], id="oversize", content="무관 " * 1000)
    retriever = ActiveMemoryRetriever(Store(items + [overflow]))
    assert retriever.retrieve("안녕") == tuple(reversed(items[-3:]))
    assert retriever.retrieve("나에 대해 말해줘") == tuple(reversed(items))


def test_profiles_confidence_then_recency_cut_whole_sentences_to_budget():
    items = list(memories((("semantic", "프로필가"), ("relationship", "프로필나"),
                           ("semantic", "프로필다"), ("semantic", "프로필라"))))
    items = [replace(m, confidence=c, created_at=t) for m, c, t in zip(
        items, (.7, .9, .9, .9), ("2026-09-14", "2026-09-12", "2026-09-13", "2026-09-14"),
        strict=True,
    )]
    selected = ActiveMemoryRetriever(Store(items), max_characters=8).retrieve("안녕")
    assert selected == (items[3], items[2])
    assert sum(len(m.content) for m in selected) == 8
    long = replace(items[3], content="긴 프로필 " * 500)
    assert long not in ActiveMemoryRetriever(Store([long] + items[:3])).retrieve("나에 대해?")


def test_2200_budget_no_ranked_three_cap_and_episodic_requires_overlap():
    items = memories(tuple(("semantic", str(i) + "가" * 299) for i in range(8)))
    selected = ActiveMemoryRetriever(Store(items)).retrieve("안녕")
    assert len(selected) == 7 and sum(len(m.content) for m in selected) == 2100
    episodic = memories((("episodic", "전시회에 갔다"),))
    assert ActiveMemoryRetriever(Store(episodic)).retrieve("나에 대해 말해줘") == episodic
    overflow = replace(episodic[0], id="oversize", content="무관 " * 1000)
    assert ActiveMemoryRetriever(Store((*episodic, overflow))).retrieve("나에 대해 말해줘") == ()
    assert ActiveMemoryRetriever(Store(episodic)).retrieve("전시회 기억나?") == episodic


def test_prose_preserves_exact_source_even_with_whitespace_and_unknown_kind():
    items = memories((("semantic", "  원문\n줄바꿈  "), ("project", "다른 종류"),
                      ("semantic", "두 번째 사실"), ("custom", "알 수 없는 종류")))
    block = memory_context(items, clock=FixedClock())
    assert all(m.content in block for m in items)
    assert block.endswith("  원문\n줄바꿈  . 두 번째 사실. 다른 종류. 알 수 없는 종류.")
    assert "[custom]" not in block and "[사실]" not in block


def test_interleaved_states_keep_individual_dates_without_changing_selection():
    items = memories((("current_state:one", "공부가 버겁다"),
                      ("semantic", "1990년생이다."),
                      ("current_state:two", "운동을 시작했다"),
                      ("semantic", "예시 연구소 선임연구원이다")))
    selected = ActiveMemoryRetriever(Store(items)).retrieve("do you know about me?")
    assert set(selected) == set(items)
    assert memory_context(items, clock=FixedClock()) == (
        MEMORY_BACKGROUND_INSTRUCTION + "\n1990년생이다. 예시 연구소 선임연구원이다. "
        "(9월 13일) 공부가 버겁다. (9월 13일) 운동을 시작했다."
    )


@pytest.mark.parametrize("facts,ending", [
    ((("semantic", "예시 연구원이다"),), "예시 연구원이다."),
    ((("current_state:one", "잠깐 쉬는 중"),), "(9월 13일) 잠깐 쉬는 중."),
    ((("current_state:one", "첫 문장. 다음 문장!"),), "(9월 13일) 첫 문장. 다음 문장!"),
])
def test_background_or_state_only_does_not_add_empty_sections(facts, ending):
    assert (memory_context(memories(facts), clock=FixedClock())
            == MEMORY_BACKGROUND_INSTRUCTION + "\n" + ending)


def test_small_store_includes_all_states_but_overflow_keeps_two_slots():
    items = memories(tuple((f"current_state:{i}", f"상태 {i}") for i in range(4)))
    assert ActiveMemoryRetriever(Store(items)).retrieve("안녕") == items
    overflow = replace(items[0], id="oversize", content="무관 " * 1000)
    assert ActiveMemoryRetriever(Store((*items, overflow))).retrieve("안녕") == (items[3], items[2])


def test_empty_and_zero_budget():
    assert ActiveMemoryRetriever(Store(())).retrieve("나에 대해?") == ()
    assert ActiveMemoryRetriever(Store(memories()), max_characters=0).retrieve("나에 대해?") == ()


@pytest.mark.parametrize("source", ["extracted", "reflection_reviewed", "user_explicit",
                                    "user_direct", "reflection_auto"])
def test_fact_sources_use_prose_only_legacy_auto_is_quoted(source):
    item = replace(memories((("semantic", "천우는 6년차 개발자다."),))[0], source=source)
    rendered = memory_context((item,), clock=FixedClock())
    expected = f'천우가 말하길 “{item.content}”.' if source == "reflection_auto" else item.content
    assert rendered == MEMORY_BACKGROUND_INSTRUCTION + "\n" + expected


def test_extracted_state_is_already_third_person_prose():
    item = replace(memories((("current_state:work", "천우는 업무로 피곤하다."),))[0],
                   source="extracted")
    assert (memory_context((item,), clock=FixedClock())
            == MEMORY_BACKGROUND_INSTRUCTION + "\n(9월 13일) " + item.content)


def test_recall_overview_precedes_other_matching_facts_when_budget_is_tight():
    items = memories((("preference", "간결한 설명"), ("project", "영어 공부"),
                      ("episodic", "예전 전시회 기록")))
    selected = ActiveMemoryRetriever(Store(items), max_characters=11).retrieve(
        "나에 대해 말해줘 전시회 기록"
    )
    assert {m.kind for m in selected} == {"preference", "project"}
