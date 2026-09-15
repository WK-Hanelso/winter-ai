"""P5.2 utterance time, Korean dates and notice watermarks; fake/temp stores only."""

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from io import StringIO
import json
import sqlite3

import pytest

from companion import fact_extraction, memory, text_reflection
from companion.adapters.fact_extractor import (
    SYSTEM_PROMPT,
    VERIFICATION_PROMPT,
    StructuredChatFactExtractor,
    extraction_schema,
    verification_schema,
)
from companion.adapters.fake import InMemoryConversationRepository
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.contracts import ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.fact_extraction import (
    Fact,
    FactExtractionRequest,
    FactExtractionService,
    SqliteFactExtractionRepository,
    _unsupported_tokens,
)
from companion.memory import (
    MEMORY_BACKGROUND_INSTRUCTION,
    ActiveMemoryRetriever,
    Memory,
    SqliteMemoryRepository,
    memory_context,
)
from companion.reflection import SqliteConversationHistoryReader

SAVED = "2026-09-14T08:40:00+00:00"
SPOKEN = "2026-08-17T16:21:15+00:00"


@dataclass
class FixedClock:
    value: datetime = datetime(2026, 9, 14, tzinfo=UTC)

    def now(self):
        return self.value


class Extractor:
    def __init__(self, facts):
        self.facts = facts
        self.requests = []

    def extract(self, request):
        self.requests.append(request)
        return self.facts

    def verify(self, request, facts):
        self.requests.append(request)
        return (True,) * len(facts)


def test_service_uses_utterance_time_for_active_candidate_and_state(tmp_path, monkeypatch):
    monkeypatch.setattr(fact_extraction, "_now", lambda: SAVED)
    monkeypatch.setattr(memory, "_now", lambda: SAVED)
    history_path = tmp_path / "conversations.sqlite"
    history = SqliteConversationRepository(history_path)
    history.append(ConversationMessage("user", "개발자야. 피곤해. 커피를 좋아해"))
    with sqlite3.connect(history_path) as connection:
        connection.execute("UPDATE conversation_messages SET created_at = ?",
                           ("2026-08-17 16:21:15",))
    repo = SqliteFactExtractionRepository(tmp_path / "memories.sqlite")
    extractor = Extractor((
        Fact("semantic", "천우는 개발자다.", .95, "durable", "개발자야"),
        Fact("current_state:wellbeing", "천우는 피곤하다.", .95, "episodic", "피곤해"),
        Fact("preference", "천우는 커피를 좋아한다.", .5, "durable", "커피를 좋아해"),
    ))
    result = FactExtractionService(
        SqliteConversationHistoryReader(history_path), repo, extractor,
    ).process_pending()
    assert (result.stored, result.failed) == (3, 0)
    assert len(extractor.requests) == 2
    assert all(r.spoken_at == SPOKEN and r.spoken_date_kst == "2026-08-18"
               for r in extractor.requests)
    assert {m.status for m in repo.memories.list()} == {"active", "candidate"}
    assert all(m.created_at == SPOKEN and m.updated_at == SAVED for m in repo.memories.list())


def test_current_state_optional_timestamp_and_history_keep_save_time(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_now", lambda: SAVED)
    repo = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    old = repo.remember_current_state(topic="work", content="쉬는 중")
    assert old.created_at == old.updated_at == SAVED
    # Exercise the public call's recursive transaction path, too.
    new = repo.remember_current_state(topic="work", content="업무 중", created_at=SPOKEN)
    assert new.created_at == SPOKEN and new.updated_at == SAVED
    assert new.supersedes == old.id
    assert repo.get(old.id).created_at == SAVED
    assert repo.get(old.id).updated_at == SAVED and repo.get(old.id).status == "deprecated"


def test_invalid_utterance_time_fails_without_generating_or_storing(tmp_path):
    path = tmp_path / "conversations.sqlite"
    SqliteConversationRepository(path).append(ConversationMessage("user", "개발자야"))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE conversation_messages SET created_at = 'invalid'")
    repo = SqliteFactExtractionRepository(tmp_path / "memories.sqlite")
    extractor = Extractor(())
    result = FactExtractionService(
        SqliteConversationHistoryReader(path), repo, extractor,
    ).process_pending()
    assert result.failed == 1 and not extractor.requests and not repo.memories.list()
    assert repo.completed_ids() == set()


def test_absolute_date_fake_response_reaches_storage_with_original_evidence(tmp_path):
    path = tmp_path / "conversations.sqlite"
    text = "어제 친구랑 전시회에 갔어"
    SqliteConversationRepository(path).append(ConversationMessage("user", text))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE conversation_messages SET created_at = '2026-09-13 14:15:00'")
    fact = Fact("episodic", "천우는 9월 12일에 친구와 전시회에 갔다.", .95, "episodic", text)

    class Model:
        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            response = {"facts": [asdict(fact)]} if len(self.requests) == 1 else {"accept": [True]}
            return ChatResult(json.dumps(response, ensure_ascii=False))

    repo = SqliteFactExtractionRepository(tmp_path / "memories.sqlite")
    model = Model()
    result = FactExtractionService(
        SqliteConversationHistoryReader(path), repo, StructuredChatFactExtractor(model),
    ).process_pending()
    assert result.stored == 1 and result.failed == 0
    assert result.lexical_rejected == result.lexical_demoted == 0
    assert len(model.requests) == 2
    for request in model.requests:
        assert json.loads(request.messages[-1].content)["spoken_at_kst"] == "2026-09-13 (일) 23:15"
    verified = json.loads(model.requests[1].messages[-1].content)["facts"][0]
    assert verified["evidence"] == text
    saved = repo.memories.list()[0]
    assert saved.content == fact.content and saved.confidence == .95
    assert saved.created_at == "2026-09-13T14:15:00+00:00"
    # The prefix denotes the utterance; the content denotes the exhibition's date.
    assert memory_context((saved,), clock=FixedClock()).endswith(
        "\n(9월 13일) 천우는 9월 12일에 친구와 전시회에 갔다."
    )


@pytest.mark.parametrize("raw,utc,kst", [
    ("2026-09-13 14:59:59", "2026-09-13T14:59:59+00:00", "2026-09-13"),
    ("2026-09-13 15:00:00", "2026-09-13T15:00:00+00:00", "2026-09-14"),
    ("2026-12-31T15:00:00Z", "2026-12-31T15:00:00+00:00", "2027-01-01"),
    ("2026-09-14T01:21:15.123456+09:00", "2026-09-13T16:21:15.123456+00:00", "2026-09-14"),
])
def test_request_normalizes_utc_and_derives_korean_date(raw, utc, kst):
    request = FactExtractionRequest(1, "합성 발화", spoken_at=raw)
    assert request.spoken_at == utc and request.spoken_date_kst == kst


@pytest.mark.parametrize("day,weekday", [(7, "월"), (8, "화"), (9, "수"), (10, "목"),
                                       (11, "금"), (12, "토"), (13, "일")])
def test_both_payloads_have_kst_date_weekday_time_without_schema_or_call_change(day, weekday):
    class Model:
        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return ChatResult('{"facts":[]}' if len(self.requests) == 1 else '{"accept":[true]}')

    model = Model()
    adapter = StructuredChatFactExtractor(model)
    request = FactExtractionRequest(1, "피곤해", spoken_at=f"2026-09-{day:02}T14:15:00+00:00")
    assert adapter.extract(request) == ()
    fact = Fact("current_state:wellbeing", "천우는 피곤하다.", .95, "episodic", "피곤해")
    assert adapter.verify(request, (fact,)) == (True,)
    assert len(model.requests) == 2
    for call in model.requests:
        payload = json.loads(call.messages[-1].content)
        assert payload["spoken_at_kst"] == f"2026-09-{day:02} ({weekday}) 23:15"
    schemas = [c.response_format["json_schema"]["schema"] for c in model.requests]
    assert schemas == [extraction_schema(), verification_schema(1)]
    assert set(schemas[0]["properties"]["facts"]["items"]["properties"]) == {
        "kind", "content", "confidence", "temporal_scope", "evidence",
    }


@pytest.mark.parametrize("dates", ["9월 12일에", "2025년 12월 31일", "월요일", "화요일에",
                                   "수요일은", "목요일", "금요일", "토요일", "일요일"])
def test_date_tokens_do_not_hide_unsupported_nouns(dates):
    request = FactExtractionRequest(1, "어제 친구랑 전시회에 갔어", spoken_at=SPOKEN)
    fact = Fact("episodic", f"천우는 {dates} 친구와 전시회에 갔다.", .95,
                "episodic", request.user_text)
    assert _unsupported_tokens(fact, request) == set()
    assert _unsupported_tokens(replace(fact, content=fact.content + " 데이터베이스 연구원"),
                               request) == {"데이터베이스", "연구원"}


def test_relative_time_prompt_rules_and_examples_are_consistent():
    for word in ("오늘", "어제", "그제", "이번 주", "지난주", "요즘", "최근", "N일 전", "다음 주"):
        assert word in SYSTEM_PROMPT
    assert "원문에 없는 절대 날짜를 계산하거나 만들지 마" not in SYSTEM_PROMPT
    assert '"content":"천우는 어제' not in SYSTEM_PROMPT
    assert '"content":"천우는 요즘' not in SYSTEM_PROMPT
    assert "연도가 발화 연도와 다를 때만" in SYSTEM_PROMPT
    assert "spoken_at_kst로 계산한 절대 날짜는 근거에 있는 정보로 인정한다" in VERIFICATION_PROMPT


def item(kind, stamp, *, source="extracted"):
    return Memory("test", kind, "천우는 합성 정보를 말했다.", 5, .95, "active", source,
                  stamp, SAVED, None, None, "conversation:1", "durable")


@pytest.mark.parametrize("source", ["extracted", "user_current_state", "reflection_auto"])
@pytest.mark.parametrize("kind,age,prefix", [
    ("episodic", 0, "(9월 14일)"),
    ("episodic", 31, "(8월 14일)"),
    ("current_state:workload", 14, "(8월 31일)"),
    ("current_state:workload", 15, "(8월 30일 기준, 지금은 다를 수 있음)"),
    ("semantic", 30, ""),
    ("semantic", 31, "(8월 14일 기준)"),
])
def test_rendering_date_thresholds_and_reflection_quotes(source, kind, age, prefix):
    clock = FixedClock()
    stamp = (clock.value - timedelta(days=age)).isoformat()
    fact = item(kind, stamp, source=source)
    body = f'천우가 말하길 “{fact.content}”.' if source == "reflection_auto" else fact.content
    expected = (prefix + " " if prefix else "") + body
    assert memory_context((fact,), clock=clock) == MEMORY_BACKGROUND_INSTRUCTION + "\n" + expected


@pytest.mark.parametrize("kind", ["semantic", "relationship", "preference", "project", "decision",
                                 "procedural"])
def test_all_durable_kinds_only_show_date_after_thirty_days(kind):
    for stamp, prefix in [("2026-08-15T00:00:00+00:00", ""),
                          ("2026-08-14T00:00:00+00:00", "(8월 14일 기준) ")]:
        fact = item(kind, stamp)
        assert memory_context((fact,), clock=FixedClock()).endswith(
            "\n" + prefix + fact.content
        )


@pytest.mark.parametrize("stamp", ["2026-08-30 15:00:00", "2026-08-30T15:00:00+00:00"])
def test_rendering_uses_kst_calendar_days_on_both_sides(stamp):
    fact = item("current_state:wellbeing", stamp)
    before = memory_context((fact,), clock=FixedClock(datetime(2026, 9, 14, 14, 59, tzinfo=UTC)))
    after = memory_context((fact,), clock=FixedClock(datetime(2026, 9, 14, 15, tzinfo=UTC)))
    assert before.endswith("\n(8월 31일) " + fact.content)  # KST 9/14: 14 days
    assert after.endswith("\n(8월 31일 기준, 지금은 다를 수 있음) " + fact.content)


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        memory_context((), clock=FixedClock(datetime(2026, 9, 14)))


@pytest.mark.parametrize("kind,query", [("semantic", "안녕"), ("relationship", "안녕"),
                                       ("episodic", "공통"), ("project", "안녕"),
                                       ("preference", "안녕"), ("current_state:work", "공통")])
def test_every_selection_pool_uses_spoken_recency(tmp_path, kind, query):
    repo = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    # Direct temp inserts permit two active state fixtures to exercise that pool's ordering.
    with sqlite3.connect(tmp_path / "memories.sqlite") as connection:
        for name, spoken, saved in [("old", SPOKEN, SAVED),
                                    ("new", "2026-09-13T00:00:00+00:00", SPOKEN)]:
            fact = replace(item(kind, spoken), id=name, content=f"공통 {name}", updated_at=saved)
            connection.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                memory._values(fact),
            )
    selected = ActiveMemoryRetriever(repo, max_characters=len("공통 new")).retrieve(query)
    assert [m.id for m in selected] == ["new"]


def test_core_passes_clock_into_memory_rendering(tmp_path):
    class Model:
        def generate(self, request):
            self.request = request
            return ChatResult("응.")

    repo = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    repo.remember_current_state(topic="work", content="천우는 쉬는 중이다.",
                                created_at="2026-08-30T00:00:00+00:00")
    model = Model()
    core = CompanionCore(model, InMemoryConversationRepository(), clock=FixedClock(),
                         memory_retriever=ActiveMemoryRetriever(repo))
    core.respond_to_text("안녕")
    assert any("(8월 30일 기준, 지금은 다를 수 있음) 천우는 쉬는 중이다." in m.content
               for m in model.request.messages)


def test_backfilled_fact_notice_uses_storage_time_once(tmp_path, monkeypatch):
    monkeypatch.setattr(fact_extraction, "_now", lambda: SAVED)
    (tmp_path / "fact_notice.json").write_text(
        json.dumps({"last_notified_at": "2026-09-14T08:00:00+00:00"}),
    )
    repo = SqliteFactExtractionRepository(tmp_path / "memories.sqlite")
    fact = Fact("semantic", "천우는 개발자다.", .95, "durable", "개발자야")
    repo.store(FactExtractionRequest(1, "개발자야", spoken_at=SPOKEN), (fact,), total=1, rejected=0)

    class NoticeDatetime:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 14, 9, tzinfo=UTC)

    monkeypatch.setattr(text_reflection, "datetime", NoticeDatetime)
    first, second = StringIO(), StringIO()
    text_reflection.notify_before_session(tmp_path, first)
    text_reflection.notify_before_session(tmp_path, second)
    assert first.getvalue() == '지난 대화에서 기억한 것 1개: “천우는 개발자다.”\n'
    assert second.getvalue() == ""
    saved = repo.memories.list()[0]
    assert saved.created_at == SPOKEN and saved.updated_at == SAVED
