"""P5 fake-model extraction, atomic storage and backfill in temporary DBs."""

from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from companion.adapters.fact_extractor import StructuredChatFactExtractor
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.contracts import ChatResult, ConversationMessage
from companion.fact_extraction import (
    Fact,
    FactExtractionService,
    SqliteFactExtractionRepository,
)
from companion.memory import ActiveMemoryRetriever, _terms
from companion.reflection import SqliteConversationHistoryReader


class Model:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return ChatResult(json.dumps(result, ensure_ascii=False))


def setup(tmp_path, text, facts, accept=None, *, context=()):
    history = SqliteConversationRepository(tmp_path / "conversations.sqlite")
    for message in context:
        history.append(message)
    history.append(ConversationMessage("user", text))
    repository = SqliteFactExtractionRepository(tmp_path / "memories.sqlite")
    model = Model({"facts": [asdict(f) for f in facts]},
                  {"accept": accept if accept is not None else [True] * len(facts)})
    service = FactExtractionService(
        SqliteConversationHistoryReader(tmp_path / "conversations.sqlite"),
        repository, StructuredChatFactExtractor(model),
    )
    return repository, model, service


def fact(content="천우는 6년차 개발자다.", evidence="6년차 개발자", **kwargs):
    return replace(Fact("semantic", content, .95, "durable", evidence), **kwargs)


def audit(tmp_path, message_id=1):
    with sqlite3.connect(tmp_path / "memories.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        return dict(connection.execute("SELECT * FROM fact_extractions WHERE message_id = ?",
                                       (message_id,)).fetchone())


def test_three_facts_store_provenance_status_and_uncertainty(tmp_path):
    facts = (fact(evidence="아마 6년차 개발자"),
             fact("천우는 커피를 좋아한다.", "커피를 좋아해", kind="preference"),
             fact("천우는 AI 개발자를 목표로 한다.", "AI 개발자가 목표야",
                  kind="project", confidence=.59))
    repo, model, service = setup(
        tmp_path, "아마 6년차 개발자고 커피를 좋아해. AI 개발자가 목표야", facts,
    )
    assert asdict(service.process_pending()) == dict(
        processed=1, stored=3, rejected=0, failed=0, lexical_rejected=0, lexical_demoted=0,
    )
    rows = repo.memories.list()
    assert [(m.kind, m.content) for m in rows] == [(f.kind, f.content) for f in facts]
    assert [m.confidence for m in rows] == [.8, .95, .59]
    assert [m.status for m in rows] == ["active", "active", "candidate"]
    assert all(m.source == "extracted" and m.evidence_ref == "conversation:1"
               and m.importance == 5 and m.temporal_scope == "durable" for m in rows)
    assert rows[2] not in ActiveMemoryRetriever(repo.memories).retrieve("나에 대해 AI")
    assert len(model.requests) == 2
    assert audit(tmp_path)["facts"] == 3


def test_invalid_substring_discarded_and_all_other_facts_verified_once(tmp_path):
    repo, model, service = setup(tmp_path, "6년차 개발자", (
        fact(), fact("천우는 AI 개발자다.", "없는 원문"),
        fact("천우는 선임 개발자다.", "6년차 개발자"),
    ), [True, False])
    result = service.process_pending()
    assert (result.stored, result.rejected, result.failed) == (1, 2, 0)
    assert len(repo.memories.list()) == 1
    assert audit(tmp_path)["rejected"] == 2
    schema = model.requests[1].response_format["json_schema"]["schema"]
    assert schema["properties"]["accept"]["minItems"] == 2
    assert schema["properties"]["accept"]["maxItems"] == 2
    assert len(model.requests) == 2


@pytest.mark.parametrize("facts", [(), (fact(evidence="원문 없음"),)])
def test_empty_or_no_grounded_facts_need_one_call(tmp_path, facts):
    repo, model, service = setup(tmp_path, "안녕", facts)
    result = service.process_pending()
    assert result.stored == 0 and result.rejected == len(facts)
    assert not repo.memories.list()
    assert len(model.requests) == 1


@pytest.mark.parametrize("content", ["천우는 Python config 개발 설계 작업을 한다.",
                                    "천우는 Python config 개발 설계 작업을 한다. 매일"])
def test_active_exact_and_jaccard_duplicates_keep_old_evidence(tmp_path, content):
    original = "천우는 Python config 개발 설계 작업을 한다."
    assert len(_terms(content) & _terms(original)) / len(_terms(content) | _terms(original)) >= .8
    repo, _, service = setup(tmp_path, content, (fact(content, content),))
    old = repo.memories.remember_explicit(kind="semantic", content=original,
                                        evidence_ref="conversation:99")
    assert service.process_pending().stored == 0
    assert repo.memories.list() == (old,)
    assert audit(tmp_path)["stored"] == audit(tmp_path)["rejected"] == 0


def test_within_message_duplicates_choose_one_across_kinds(tmp_path):
    f = fact()
    repo, _, service = setup(tmp_path, f.evidence, (f, f, replace(f, kind="project")))
    assert service.process_pending().stored == 1
    assert {m.kind for m in repo.memories.list()} == {"semantic"}


def test_state_history_and_low_confidence_preserve_active_state(tmp_path):
    states = (fact("천우는 업무로 피곤하다.", "업무로 피곤해", kind="current_state:workload",
                   temporal_scope="episodic"),
              fact("천우는 쉬고 싶다.", "쉬고 싶어", kind="current_state:workload",
                   temporal_scope="episodic", confidence=.5))
    repo, _, service = setup(tmp_path, "업무로 피곤해. 쉬고 싶어", states)
    old = repo.memories.remember_current_state(topic="workload", content="천우는 업무가 적다.")
    assert service.process_pending().stored == 2
    assert repo.memories.get(old.id).status == "deprecated"
    new = repo.memories.list_active()[0]
    assert new.supersedes == old.id and new.content == states[0].content
    assert new.evidence_ref == "conversation:1" and new.confidence == .95 and new.importance == 5
    assert new.source == "extracted"
    assert len(repo.memories.list_current_state_history(topic="workload")) == 2
    assert repo.memories.list()[-1].status == "candidate"


def test_completed_restart_skips_model_and_failed_retries(tmp_path):
    repo, model, service = setup(tmp_path, "6년차 개발자", (fact(),))
    model.responses = [RuntimeError("private data must not enter audit")]
    assert service.process_pending().failed == 1
    assert audit(tmp_path)["status"] == "failed"
    assert audit(tmp_path)["error"] == "RuntimeError"
    model.responses = [{"facts": [asdict(fact())]}, {"accept": [True]}]
    assert service.process_pending().stored == 1
    assert audit(tmp_path)["status"] == "completed" and audit(tmp_path)["error"] is None
    restarted = FactExtractionService(
        SqliteConversationHistoryReader(tmp_path / "conversations.sqlite"),
        SqliteFactExtractionRepository(tmp_path / "memories.sqlite"),
        StructuredChatFactExtractor(model),
    )
    assert restarted.process_pending().processed == 0
    assert len(model.requests) == 3 and len(repo.memories.list()) == 1


@pytest.mark.parametrize("accept", [[], [True, True], [1], ["true"], None])
def test_malformed_verifier_is_retryable_without_partial_storage(tmp_path, accept):
    repo, model, service = setup(tmp_path, "6년차 개발자", (fact(),))
    model.responses[1] = {"accept": accept}
    assert service.process_pending().failed == 1
    assert not repo.memories.list() and audit(tmp_path)["status"] == "failed"
    assert len(model.requests) == 2


@pytest.mark.parametrize("change", [
    {"kind": "invented"}, {"kind": "current_state:Upper"}, {"confidence": True},
    {"confidence": 1.1}, {"confidence": float("nan")}, {"temporal_scope": "stable"},
    {"content": " "}, {"evidence": ""}, {"content": 42}, {"extra": "field"},
])
def test_invalid_model_fact_fails_before_verification(tmp_path, change):
    repo, model, service = setup(tmp_path, "6년차 개발자", (fact(),))
    model.responses[0]["facts"][0].update(change)
    assert service.process_pending().failed == 1
    assert not repo.memories.list() and len(model.requests) == 1


def test_context_six_active_summary_thirty_and_oldest_first_resume(tmp_path):
    history = SqliteConversationRepository(tmp_path / "conversations.sqlite")
    for i in range(10):
        history.append(ConversationMessage("user" if i % 2 == 0 else "assistant", str(i)))
    repo = SqliteFactExtractionRepository(tmp_path / "memories.sqlite")
    for i in range(35):
        repo.memories.remember_explicit(kind="semantic", content=f"서로 다른 사실 {i}")
    repo.memories.remember_current_state(topic="workload", content="현재 상태")
    model = Model(*({"facts": []} for _ in range(5)))
    service = FactExtractionService(
        SqliteConversationHistoryReader(tmp_path / "conversations.sqlite"),
        repo, StructuredChatFactExtractor(model),
    )
    progress = []
    result = service.process_pending(limit=2, progress=lambda i, n: progress.append((i, n)))
    assert result.processed == 2
    assert progress == [(1, 2), (2, 2)]
    assert service.process_pending(limit=None).processed == 3
    payloads = [json.loads(r.messages[-1].content) for r in model.requests]
    assert [p["current_user_text"] for p in payloads] == ["0", "2", "4", "6", "8"]
    assert [m["content"] for m in payloads[-1]["recent_context"]] == list("234567")
    assert all(len(p["active_memories"]) == 30 for p in payloads)
    assert payloads[-1]["current_state_topics"] == ["workload"]
    schema = model.requests[0].response_format
    assert schema["type"] == "json_schema" and schema["json_schema"]["strict"] is True
    assert schema["json_schema"]["schema"]["properties"]["facts"]["maxItems"] == 5


def test_transaction_failure_rolls_back_facts_and_state_history(tmp_path):
    repo, _, service = setup(tmp_path, "피곤해 개발자", (
        fact("천우는 피곤하다.", "피곤해", kind="current_state:workload"),
        fact(evidence="개발자"),
    ))
    old = repo.memories.remember_current_state(topic="workload", content="건강함")
    with sqlite3.connect(tmp_path / "memories.sqlite") as connection:
        connection.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON memories
            WHEN NEW.kind = 'semantic' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
    assert service.process_pending().failed == 1
    assert repo.memories.list() == (old,)
    assert audit(tmp_path)["status"] == "failed"
    with sqlite3.connect(tmp_path / "memories.sqlite") as connection:
        connection.execute("DROP TRIGGER fail_second")


def test_completed_audit_preserved_after_forget_and_late_failure(tmp_path):
    repo, _, service = setup(tmp_path, "6년차 개발자", (fact(),))
    service.process_pending()
    saved = audit(tmp_path)
    repo.memories.forget_explicit("6년차 개발자")
    repo.record_failure(1, "late competing run")
    assert audit(tmp_path) == saved
    assert service.process_pending().processed == 0
    assert not repo.memories.list_active()


def test_completed_different_version_is_reprocessed(tmp_path):
    repo, _, service = setup(tmp_path, "6년차 개발자", (fact(),))
    with sqlite3.connect(tmp_path / "memories.sqlite") as connection:
        connection.execute(
            "INSERT INTO fact_extractions VALUES (1, 0, 'completed', 0, 0, 0, NULL, '')",
        )
    assert service.process_pending().stored == 1
    assert audit(tmp_path)["version"] == 1


def test_at_most_five_and_existing_memory_table_schema_is_unchanged(tmp_path):
    from companion.memory import SqliteMemoryRepository

    path = tmp_path / "memories.sqlite"
    original = SqliteMemoryRepository(path)
    old = original.remember_explicit(kind="semantic", content="원래 프로필")
    with sqlite3.connect(path) as connection:
        before = connection.execute("PRAGMA table_info(memories)").fetchall()
    repo, model, service = setup(tmp_path, "6년차 개발자", (fact(),) * 6)
    assert service.process_pending().failed == 1
    assert len(model.requests) == 1
    assert repo.memories.list() == (old,)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA table_info(memories)").fetchall() == before


# P5.1: only the supplied excerpts are real-message fixtures. Missing text/context
# for 525/529/543 is not reconstructed; their failure shapes use synthetic examples.
@pytest.mark.parametrize("text,content,kind", [
    ("yes you are right. but now you don't understand my meaning e…",
     "천우는 데이터베이스에 관한 정보를 나열하는 방식으로 설명하고 싶어 한다.", "semantic"),
    ("야 그만 반복하라고 뭐하는거야 대체",
     "천우는 반복되는 대화 방식에 대해 불만을 느끼고 있다.", "semantic"),
    ("계속 잘한다 잘한다 하는데 너무 성의없이…",
     "천우는 말투가 너무 성의 없이 느껴진다고 생각한다.", "semantic"),
    ("…혹시 겨울이는 인터넷 검색 같은거 가능해?",
     "천우는 겨울이가 인터넷 검색 같은 기능을 수행할 수 있는지 묻고 있다.", "procedural"),
    ("야 그만 반복하라고 뭐하는거야 대체",
     "천우는 반복되는 대화 방식에 대해 불만을 느끼고 있다.",
     "current_state:communication_style"),
    ("겨울이를 감정 쓰레기통으로 사용하려는 게 아니다",  # Synthetic negative-intent clause.
     "천우는 겨울이를 자신의 감정 쓰레기통으로 사용하려는 의도가 없다고 말한다.", "semantic"),
    ("응 맞아 그렇게 말했어",  # Korean evidence with two invented content nouns.
     "천우는 데이터베이스 개발자다.", "semantic"),
])
def test_p51_dry_run_failure_shapes_never_reach_verifier(tmp_path, text, content, kind):
    repo, model, service = setup(tmp_path, text, (fact(content, text, kind=kind),))
    result = service.process_pending()
    assert (result.stored, result.rejected, result.failed) == (0, 1, 0)
    assert len(model.requests) == 1 and not repo.memories.list()
    assert audit(tmp_path)["rejected"] == 1
    if "개발자다" in content:
        assert result.lexical_rejected == 1


@pytest.mark.parametrize("word", [
    "느낀다", "느끼고 있다", "생각한다", "불만이다", "의도다", "말한다", "원한다", "바란다",
])
def test_p51_semantic_evaluation_is_discarded_without_reclassification(tmp_path, word):
    content = f"천우는 개발자라고 {word}."
    repo, model, service = setup(tmp_path, content, (fact(content, content),))
    assert service.process_pending().rejected == 1
    assert not repo.memories.list() and len(model.requests) == 1


@pytest.mark.parametrize("question", [
    "인터넷 검색 가능해?", "인터넷 검색 가능해", "검색 기능 있어", "인터넷 검색 알아",
    "검색이 뭐야", "인터넷 검색 가능해요", "검색 가능해？",
])
def test_p51_question_evidence_is_rejected(tmp_path, question):
    repo, model, service = setup(tmp_path, question, (
        fact("천우는 인터넷 검색을 한다.", question, kind="procedural"),
    ))
    assert service.process_pending().rejected == 1
    assert not repo.memories.list() and len(model.requests) == 1


def test_p51_mixed_question_extracts_only_direct_clause(tmp_path):
    text = "나는 6년차 개발자야. 혹시 겨울이는 인터넷 검색 같은거 가능해?"
    repo, model, service = setup(tmp_path, text, (
        fact(evidence="나는 6년차 개발자야"),
        fact("천우는 인터넷 검색을 한다.", "혹시 겨울이는 인터넷 검색 같은거 가능해?",
             kind="procedural"),
    ), [True])
    assert service.process_pending().stored == 1
    assert repo.memories.list()[0].kind == "semantic" and len(model.requests) == 2
    assert len(json.loads(model.requests[1].messages[-1].content)["facts"]) == 1


@pytest.mark.parametrize("topic", [
    "communication_style", "conversation_quality", "reply_length", "verbal_style", "winter_tone",
])
def test_p51_forbidden_state_topics_cannot_deprecate_old_state(tmp_path, topic):
    text = "더 친구처럼 말해줘"
    repo, model, service = setup(tmp_path, text, (fact(text, text, kind=f"current_state:{topic}"),))
    old = repo.memories.remember_current_state(topic=topic, content="기존 상태")
    assert service.process_pending().stored == 0
    assert repo.memories.list() == (old,) and len(model.requests) == 1
    assert repo.memories.list_current_state_history(topic=topic) == (old,)


@pytest.mark.parametrize("kind", ["preference", "relationship", "current_state:wellbeing"])
def test_p51_feedback_cannot_hide_in_another_kind(tmp_path, kind):
    text = "계속 잘한다 잘한다 하는데 너무 성의없이…"
    content = "천우는 말투가 너무 성의 없이 느껴진다고 생각한다."
    repo, model, service = setup(tmp_path, text, (fact(content, text, kind=kind),))
    assert service.process_pending().rejected == 1
    assert not repo.memories.list() and len(model.requests) == 1


@pytest.mark.parametrize("text", [
    "겨울이를 감정 쓰레기통으로 사용하려는 게 아니다", "겨울이를 바꿀 의도가 없다",
    "겨울이를 감정 쓰레기통으로 사용하지 않는다",
])
def test_p51_negative_intent_is_discarded_in_nonsemantic_kinds(tmp_path, text):
    repo, model, service = setup(tmp_path, text, (fact(text, text, kind="relationship"),))
    assert service.process_pending().rejected == 1
    assert not repo.memories.list() and len(model.requests) == 1


@pytest.mark.parametrize("text", ["더 친구처럼 말해줘", "더 많이 물어봐줘", "영어 선생이 돼줘"])
def test_p51_concrete_request_four_kinds_become_one_preference(tmp_path, text):
    facts = tuple(fact(text, text, kind=kind) for kind in (
        "relationship", "preference", "current_state:communication_style",
        "current_state:wellbeing",
    ))
    repo, model, service = setup(tmp_path, text, facts, [True] * 3)
    result = service.process_pending()
    assert (result.stored, result.rejected, result.failed) == (1, 1, 0)
    assert repo.memories.list()[0].kind == "preference" and len(model.requests) == 2


def test_p51_lexical_zero_one_two_and_audit_counts(tmp_path):
    repo, model, service = setup(tmp_path, "6년차 개발자. 연구원", (
        fact(), fact("천우는 선임 연구원이다.", "연구원"),
        fact("천우는 선임 데이터베이스 연구원이다.", "연구원"),
    ), [True, True])
    result = service.process_pending()
    assert asdict(result) == dict(processed=1, stored=2, rejected=1, failed=0,
                                 lexical_rejected=1, lexical_demoted=1)
    rows = repo.memories.list()
    assert [(m.confidence, m.status) for m in rows] == [(.95, "active"), (.55, "candidate")]
    assert rows[1] not in ActiveMemoryRetriever(repo.memories).retrieve("선임 연구원")
    assert len(model.requests) == 2
    payload = json.loads(model.requests[1].messages[-1].content)
    assert len(payload["facts"]) == 2 and payload["facts"][1]["confidence"] == .55
    assert audit(tmp_path)["rejected"] == 1
    assert set(audit(tmp_path)) == {
        "message_id", "version", "status", "facts", "stored", "rejected", "error", "created_at",
    }


@pytest.mark.parametrize("confidence", [.4, .55, .6, .95])
def test_p51_lexical_demotion_never_increases_confidence(tmp_path, confidence):
    repo, _, service = setup(tmp_path, "개발자", (
        fact("천우는 선임 개발자다.", "개발자", confidence=confidence),
    ))
    assert service.process_pending().lexical_demoted == 1
    assert repo.memories.list()[0].confidence == min(.55, confidence)
    assert repo.memories.list()[0].status == "candidate"


@pytest.mark.parametrize("evidence,content", [
    ("최합", "천우는 최종 합격했다."),
    ("커피를 좋아해", "천우는 커피를 좋아한다."),
    ("업무로 피곤해", "천우는 업무로 피곤하다."),
    ("이직 준비 중이야", "천우는 이직 준비 중이다."),
    ("최종 합격했고 연봉 협상 중이야", "천우는 최종 합격했고 연봉 협상 중이다."),
])
def test_p51_supported_prefix_abbreviation_and_life_facts(tmp_path, evidence, content):
    repo, _, service = setup(tmp_path, evidence, (fact(content, evidence, kind="project"),))
    result = service.process_pending()
    assert result.stored == 1 and result.lexical_demoted == result.lexical_rejected == 0
    assert repo.memories.list()[0].status == "active"


def test_p51_abbreviation_does_not_support_other_invented_terms(tmp_path):
    repo, model, service = setup(tmp_path, "최합", (
        fact("천우는 최종 합격했고 연봉 협상 중이다.", "최합", kind="project"),
    ))
    assert service.process_pending().lexical_rejected == 1
    assert not repo.memories.list() and len(model.requests) == 1


@pytest.mark.parametrize("with_context", [False, True])
def test_p51_lexical_support_uses_recent_context_not_other_current_clauses(tmp_path, with_context):
    context = (ConversationMessage("assistant", "데이터베이스 선임"),) if with_context else ()
    repo, _, service = setup(tmp_path, "데이터베이스 선임. 개발자야", (
        fact("천우는 데이터베이스 선임 개발자다.", "개발자야"),
    ), context=context)
    result = service.process_pending()
    assert result.stored == int(with_context)
    assert result.lexical_rejected == int(not with_context)
    assert len(repo.memories.list()) == int(with_context)


@pytest.mark.parametrize("winner", ["semantic", "project", "relationship", "preference",
                                    "decision", "procedural", "episodic", "current_state:workload"])
def test_p51_cross_kind_confidence_and_tie_priority(tmp_path, winner):
    priority = ["semantic", "project", "relationship", "preference", "decision", "procedural",
                "episodic", "current_state:workload"]
    kinds = priority[priority.index(winner):][:4]
    facts = tuple(fact(kind=k) for k in reversed(kinds))
    repo, _, service = setup(tmp_path, "6년차 개발자", facts)
    assert service.process_pending().stored == 1
    assert repo.memories.list()[0].kind == winner


def test_p51_cross_kind_highest_confidence_wins_even_for_candidates(tmp_path):
    repo, _, service = setup(tmp_path, "6년차 개발자", (
        fact(confidence=.4), fact(kind="project", confidence=.5),
        fact(kind="relationship", confidence=.3), fact(kind="preference", confidence=.2),
    ))
    assert service.process_pending().stored == 1
    assert repo.memories.list()[0].kind == "project"
    assert repo.memories.list()[0].status == "candidate"


def test_p51_rejected_higher_confidence_does_not_hide_verified_duplicate(tmp_path):
    repo, _, service = setup(tmp_path, "6년차 개발자", (
        fact(), fact(kind="project", confidence=.7),
    ), [False, True])
    assert service.process_pending().stored == 1
    assert repo.memories.list()[0].kind == "project"


def test_p51_existing_active_duplicate_is_kind_independent(tmp_path):
    text = "천우는 6년차 개발자다."
    repo, _, service = setup(tmp_path, text, (fact(kind="project"),))
    old = repo.memories.remember_explicit(kind="semantic", content=text,
                                        evidence_ref="conversation:99")
    assert service.process_pending().stored == 0
    assert repo.memories.list() == (old,) and audit(tmp_path)["rejected"] == 0


@pytest.mark.parametrize("old,new,stored", [
    ("사과 바나나 포도 딸기", "사과 바나나 포도 수박", 1),  # Jaccard = .6
    ("사과 바나나 포도", "사과 바나나 수박", 2),  # Jaccard = .5
])
def test_p51_within_message_jaccard_boundary(tmp_path, old, new, stored):
    repo, _, service = setup(tmp_path, f"{old}. {new}", (
        fact(old, old, kind="project", confidence=.7),
        fact(new, new, kind="preference", confidence=.9),
    ))
    assert service.process_pending().stored == stored
    if stored == 1:
        assert repo.memories.list()[0].content == new


@pytest.mark.parametrize("new,stored", [
    ("alpha beta gamma delta epsilon", 0),  # 4/5 = .8
    ("alpha beta gamma epsilon", 1),  # 3/5 = .6
])
def test_p51_existing_cross_kind_jaccard_boundary(tmp_path, new, stored):
    repo, _, service = setup(tmp_path, new, (fact(new, new, kind="project"),))
    old = repo.memories.remember_explicit(kind="semantic", content="alpha beta gamma delta")
    assert service.process_pending().stored == stored
    assert repo.memories.get(old.id) == old


def test_p51_failed_verification_does_not_count_uncommitted_lexical_decisions(tmp_path):
    facts = (fact(), fact("천우는 선임 연구원이다.", "연구원"),
             fact("천우는 선임 데이터베이스 연구원이다.", "연구원"))
    repo, model, service = setup(tmp_path, "6년차 개발자. 연구원", facts, [])
    result = service.process_pending()
    assert result.failed == 1 and not repo.memories.list()
    assert result.rejected == result.lexical_rejected == result.lexical_demoted == 0
    assert audit(tmp_path)["status"] == "failed"
    model.responses = [{"facts": [asdict(f) for f in facts]}, {"accept": [True, True]}]
    result = service.process_pending()
    assert asdict(result) == dict(processed=1, stored=2, rejected=1, failed=0,
                                 lexical_rejected=1, lexical_demoted=1)


def test_p51_repeated_feedback_leaves_state_history_untouched(tmp_path):
    text = "야 그만 반복하라고 뭐하는거야 대체"
    f = fact("천우는 반복되는 대화 방식에 대해 불만을 느끼고 있다.", text,
             kind="current_state:communication_style")
    repo, model, service = setup(tmp_path, text, (f,))
    history = SqliteConversationRepository(tmp_path / "conversations.sqlite")
    for _ in range(3):
        history.append(ConversationMessage("user", text))
    model.responses = [{"facts": [asdict(f)]} for _ in range(4)]
    old = repo.memories.remember_current_state(topic="communication_style", content="기존 상태")
    result = service.process_pending()
    assert (result.processed, result.stored, result.rejected, result.failed) == (4, 0, 4, 0)
    assert repo.memories.list() == (old,)
    assert repo.memories.list_current_state_history(topic="communication_style") == (old,)
    assert len(model.requests) == 4


def test_p51_repeated_learning_is_a_life_state_not_winter_feedback(tmp_path):
    text = "요즘 반복 학습 중이야"
    repo, _, service = setup(tmp_path, text, (
        fact("천우는 요즘 반복 학습 중이다.", text, kind="current_state:learning",
             temporal_scope="episodic"),
    ))
    result = service.process_pending()
    assert result.stored == 1 and result.lexical_rejected == result.lexical_demoted == 0
    assert repo.memories.list()[0].status == "active"


@pytest.mark.parametrize("text, content, kind", [
    ("나야 뭐 이직 준비하고 있지 알고 있지 않았어?", "천우는 이직 준비를 하고 있다.", "project"),
    ("나 한양이엔지 최합했지만 지금 연봉협상중이라는거 몰랐어?",
     "천우는 한양이엔지에 최종 합격했고 연봉 협상 중이다.", "project"),
    ("감정 쓰레기통이 아니라 감정을 갖는 AI를 만들려고 하는거야",
     "천우는 감정을 갖는 AI를 만들려고 한다.", "project"),
    ("나는 AI개발자를 위해 더욱 노력중이지 그런데 너는 내가 원하는걸 알고 싶은 이유가 뭐야?",
     "천우는 AI 개발자를 위해 노력 중이다.", "project"),
])
def test_p51b_rhetorical_question_and_contrast_keep_the_fact(tmp_path, text, content, kind):
    repo, model, service = setup(tmp_path, text, (fact(content, text, kind=kind),))
    result = service.process_pending(limit=1)
    assert result.stored == 1
    assert [m.content for m in repo.memories.list_active()] == [content]


def test_p51b_english_evidence_skips_lexical_grounding(tmp_path):
    text = "i want you to be my english teacher too. and be my friend."
    repo, model, service = setup(tmp_path, text, (
        fact("천우는 겨울이가 영어 선생이자 친구이길 바란다.", text, kind="relationship"),
    ))
    result = service.process_pending(limit=1)
    assert result.stored == 1 and result.lexical_rejected == 0 and result.lexical_demoted == 1
    assert [m.status for m in repo.memories.list()] == ["candidate"]
