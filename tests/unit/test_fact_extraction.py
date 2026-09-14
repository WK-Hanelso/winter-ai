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


def setup(tmp_path, text, facts, accept=None):
    history = SqliteConversationRepository(tmp_path / "conversations.sqlite")
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
    assert asdict(service.process_pending()) == dict(processed=1, stored=3, rejected=0, failed=0)
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
    repo, _, service = setup(tmp_path, "개발 설계", (fact(content, "개발 설계"),))
    old = repo.memories.remember_explicit(kind="semantic", content=original,
                                        evidence_ref="conversation:99")
    assert service.process_pending().stored == 0
    assert repo.memories.list() == (old,)
    assert audit(tmp_path)["stored"] == audit(tmp_path)["rejected"] == 0


def test_within_message_duplicates_and_cross_kind_are_distinct(tmp_path):
    f = fact()
    repo, _, service = setup(tmp_path, f.evidence, (f, f, replace(f, kind="project")))
    assert service.process_pending().stored == 2
    assert {m.kind for m in repo.memories.list()} == {"semantic", "project"}


def test_state_history_and_low_confidence_preserve_active_state(tmp_path):
    states = (fact("천우는 업무로 피곤하다.", "피곤해", kind="current_state:workload",
                   temporal_scope="episodic"),
              fact("천우는 쉬고 싶다.", "쉬고 싶어", kind="current_state:workload",
                   temporal_scope="episodic", confidence=.5))
    repo, _, service = setup(tmp_path, "피곤해. 쉬고 싶어", states)
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
