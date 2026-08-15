from io import StringIO
from pathlib import Path
import sqlite3

from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.contracts import ConversationMessage
from companion.memory import SqliteMemoryRepository
from companion.outcome import SqliteOutcomeRepository
from companion.reflection import (
    ConversationHistoryBackfill,
    ConversationReflectionService,
    FakeReflectionInterpreter,
    ReflectionExtraction,
    ReflectionExtractionRequest,
    ReflectionInterpretationError,
    SqliteConversationHistoryReader,
    SqliteReflectionRepository,
)
from companion.reflection_review import (
    print_companion_overview,
    run_memory_candidate_review,
)
from companion.turn_understanding import (
    SqliteTurnUnderstandingRepository,
    TurnInterpretationRequest,
    TurnUnderstanding,
)


def test_history_backfill_links_existing_turn_and_enqueues_only_missing_once(
    tmp_path: Path,
) -> None:
    conversations_path = tmp_path / "conversations.sqlite"
    SqliteConversationRepository(conversations_path)
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    existing = turns.enqueue(TurnInterpretationRequest("첫 질문"), source="cli")
    with sqlite3.connect(conversations_path) as connection:
        connection.execute(
            "INSERT INTO conversation_messages (role, content, created_at) VALUES (?, ?, ?)",
            ("user", "첫 질문", existing.created_at),
        )
        connection.execute(
            "INSERT INTO conversation_messages (role, content, created_at) VALUES (?, ?, ?)",
            ("assistant", "첫 답변", "2026-08-15 11:00:01"),
        )
        connection.execute(
            "INSERT INTO conversation_messages (role, content, created_at) VALUES (?, ?, ?)",
            ("user", "둘째 질문", "2026-08-15 11:00:02"),
        )
    backfill = ConversationHistoryBackfill(
        SqliteConversationHistoryReader(conversations_path), turns
    )

    first = backfill.run()
    second = backfill.run()

    assert first.user_messages == 2
    assert first.linked_existing == 1
    assert first.enqueued == 1
    assert second.already_linked == 2
    assert second.linked_existing == 0
    assert second.enqueued == 0
    assert len(turns.list()) == 2
    recovered = turns.recovered_event_for(3)
    assert recovered is not None
    assert recovered.source == "history"
    assert [message.content for message in recovered.request.context] == [
        "첫 질문",
        "첫 답변",
        "둘째 질문",
    ]


def test_completed_turn_becomes_evidence_backed_candidate_without_activation(
    tmp_path: Path,
) -> None:
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    event = turns.enqueue(TurnInterpretationRequest("나는 SWM에서 일해"), source="cli")
    turns.complete(event.id, _understanding("나는 SWM에서 일해"))
    reflections = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")

    first = reflections.harvest(turns.list(status="completed"))
    second = reflections.harvest(turns.list(status="completed"))

    assert (first.processed_turns, first.created_candidates) == (1, 1)
    assert second.processed_turns == 0
    candidate = reflections.list(status="candidate")[0]
    assert candidate.content == "천우는 SWM에서 일한다."
    assert candidate.evidence[0].quote == "나는 SWM에서 일해"
    assert memories.list_active() == ()


def test_user_review_activates_only_a_stable_user_memory(tmp_path: Path) -> None:
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    event = turns.enqueue(TurnInterpretationRequest("나는 SWM에서 일해"), source="cli")
    turns.complete(event.id, _understanding("나는 SWM에서 일해"))
    reflections = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    reflections.harvest(turns.list(status="completed"))
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    output = StringIO()

    exit_code = run_memory_candidate_review(
        reflections,
        memories,
        limit=10,
        stdin=StringIO("1\n"),
        stdout=output,
    )

    assert exit_code == 0
    memory = memories.list_active()[0]
    assert memory.content == "천우는 SWM에서 일한다."
    assert memory.source == "reflection_reviewed"
    assert reflections.list(status="active")[0].memory_id == memory.id
    assert "실제 대화 근거" in output.getvalue()
    assert "다음 대화부터 겨울이가 사용할 수 있습니다" in output.getvalue()


def test_user_can_link_misclassified_candidate_to_retyped_current_state(
    tmp_path: Path,
) -> None:
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    text = "요즘 마음이 힘들어"
    event = turns.enqueue(TurnInterpretationRequest(text), source="cli")
    turns.complete(event.id, _understanding(text))
    reflections = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    reflections.harvest(turns.list(status="completed"))
    candidate = reflections.list(status="candidate")[0]
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    state = memories.remember_current_state(
        topic="wellbeing",
        content="천우는 요즘 마음이 힘들다.",
    )

    resolved = reflections.link_user_approved_memory(candidate.id, state)

    assert resolved.status == "active"
    assert resolved.memory_id == state.id


def test_non_stable_candidate_is_confirmed_but_not_used_as_memory(
    tmp_path: Path,
) -> None:
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    text = "요즘 운동을 시작했어"
    event = turns.enqueue(TurnInterpretationRequest(text), source="cli")
    payload = _understanding(text).to_dict()
    payload["memory_proposals"][0]["kind"] = "episodic"
    payload["memory_proposals"][0]["content"] = "천우가 최근 운동을 시작했다."
    payload["memory_proposals"][0]["temporal_scope"] = "episodic"
    turns.complete(event.id, TurnUnderstanding.from_dict(payload, source_text=text))
    reflections = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    reflections.harvest(turns.list(status="completed"))
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")

    run_memory_candidate_review(
        reflections,
        memories,
        limit=10,
        stdin=StringIO("1\n"),
        stdout=StringIO(),
    )

    assert reflections.list(status="approved")[0].memory_id is None
    assert memories.list_active() == ()


def test_overview_distinguishes_raw_pending_candidates_and_active_memory(
    tmp_path: Path,
) -> None:
    conversations_path = tmp_path / "conversations.sqlite"
    conversations = SqliteConversationRepository(conversations_path)
    conversations.append(ConversationMessage("user", "안녕"))
    conversations.append(ConversationMessage("assistant", "안녕 천우야"))
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    turns.enqueue(TurnInterpretationRequest("안녕"), source="cli")
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    reflections = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    memories.remember_reviewed(kind="semantic", content="천우는 개발자다")
    output = StringIO()

    print_companion_overview(
        SqliteConversationHistoryReader(conversations_path),
        turns,
        outcomes,
        reflections,
        memories,
        output,
    )

    text = output.getvalue()
    assert "대화 원문: 2개 (천우 1, 겨울이 1)" in text
    assert "대화 이해: 완료 0, 분석 대기 1, 실패 0" in text
    assert "기억 정리: 완료 0, 분석 대기 1, 재시도 필요 0" in text
    assert "천우가 볼 기억 후보: 0" in text
    assert "현재 사용 중인 장기 기억: 1" in text


def _understanding(text: str) -> TurnUnderstanding:
    return TurnUnderstanding.from_dict(
        {
            "schema_version": 1,
            "literal_meaning": "천우가 자신의 직장 정보를 말했다.",
            "observations": [
                {"content": "직장에 대한 직접 진술", "evidence": [text]}
            ],
            "intent_hypotheses": [],
            "affect": [],
            "conversational_need": "acknowledge_fact",
            "temporal_scope": "candidate_stable",
            "response_contract": {"must": [], "avoid": []},
            "memory_proposals": [
                {
                    "kind": "semantic",
                    "content": "천우는 SWM에서 일한다.",
                    "confidence": 0.95,
                    "temporal_scope": "candidate_stable",
                    "evidence": [text],
                }
            ],
            "open_loops": [],
            "project_signals": [],
            "improvement_signals": [],
            "uncertainties": [],
        },
        source_text=text,
    )


def test_malformed_reflection_is_retried_and_can_complete(tmp_path: Path) -> None:
    class FailOnceInterpreter:
        def __init__(self) -> None:
            self.calls = 0

        def extract(self, request: ReflectionExtractionRequest) -> ReflectionExtraction:
            self.calls += 1
            if self.calls == 1:
                raise ReflectionInterpretationError("truncated JSON")
            return FakeReflectionInterpreter().extract(request)

    conversations_path = tmp_path / "conversations.sqlite"
    conversations = SqliteConversationRepository(conversations_path)
    conversations.append(ConversationMessage("user", "나는 SWM에서 일해"))
    reflections = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    interpreter = FailOnceInterpreter()
    service = ConversationReflectionService(
        SqliteConversationHistoryReader(conversations_path),
        reflections,
        interpreter,
    )

    first = service.process_pending()
    second = service.process_pending()

    assert (first.failed, first.completed) == (1, 0)
    assert (second.failed, second.completed) == (0, 1)
    assert reflections.extraction_counts(user_messages=1) == {
        "completed": 1,
        "failed": 0,
        "pending": 0,
    }


def test_compact_reflection_receives_recent_conversation_context(tmp_path: Path) -> None:
    class CapturingInterpreter:
        def __init__(self) -> None:
            self.requests: list[ReflectionExtractionRequest] = []

        def extract(self, request: ReflectionExtractionRequest) -> ReflectionExtraction:
            self.requests.append(request)
            return ReflectionExtraction(())

    conversations_path = tmp_path / "conversations.sqlite"
    conversations = SqliteConversationRepository(conversations_path)
    conversations.append(ConversationMessage("user", "첫 질문"))
    conversations.append(ConversationMessage("assistant", "첫 답변"))
    conversations.append(ConversationMessage("user", "후속 질문"))
    reflections = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    interpreter = CapturingInterpreter()

    ConversationReflectionService(
        SqliteConversationHistoryReader(conversations_path),
        reflections,
        interpreter,
    ).process_pending(limit=1)

    request = interpreter.requests[0]
    assert request.user_text == "후속 질문"
    assert request.recent_context == (
        ConversationMessage("user", "첫 질문"),
        ConversationMessage("assistant", "첫 답변"),
    )


def test_compact_reflection_reports_batch_progress(tmp_path: Path) -> None:
    conversations_path = tmp_path / "conversations.sqlite"
    conversations = SqliteConversationRepository(conversations_path)
    conversations.append(ConversationMessage("user", "첫 질문"))
    conversations.append(ConversationMessage("assistant", "첫 답변"))
    conversations.append(ConversationMessage("user", "둘째 질문"))
    reflections = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    progress: list[tuple[int, int]] = []

    ConversationReflectionService(
        SqliteConversationHistoryReader(conversations_path),
        reflections,
        FakeReflectionInterpreter(),
    ).process_pending(limit=2, progress=lambda done, total: progress.append((done, total)))

    assert progress == [(1, 2), (2, 2)]
