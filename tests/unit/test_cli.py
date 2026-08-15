from argparse import Namespace
from io import StringIO
from pathlib import Path

from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.beliefs import SqliteBeliefRepository
from companion.cli.__main__ import run
from companion.contracts import ConversationMessage
from companion.memory import SqliteMemoryRepository
from companion.outcome import OutcomeAssessment, SqliteOutcomeRepository
from companion.reflection import SqliteReflectionRepository
from companion.turn_understanding import (
    FakeTurnInterpreter,
    SqliteTurnUnderstandingRepository,
    TurnInterpretationRequest,
)


def test_cli_fake_backend_runs_one_turn() -> None:
    output = StringIO()

    exit_code = run(
        Namespace(backend="fake", model_url="http://unused", prompt="안녕"),
        stdout=output,
    )

    assert exit_code == 0
    assert output.getvalue() == "Companion> fake: 안녕\n"


def test_cli_lists_current_state_history_without_a_model(tmp_path: Path) -> None:
    database = tmp_path / "memories.sqlite"
    memories = SqliteMemoryRepository(database)
    previous = memories.remember_current_state(
        topic="wellbeing",
        content="천우는 마음이 힘들었다.",
    )
    current = memories.remember_current_state(
        topic="wellbeing",
        content="천우는 운동하며 회복 중이다.",
    )
    output = StringIO()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            memory_db=database,
            list_current_state_history=True,
        ),
        stdout=output,
    )

    assert exit_code == 0
    text = output.getvalue()
    assert "천우의 현재 상태 History" in text
    assert previous.content in text and "deprecated" in text
    assert current.content in text and "active" in text


def test_cli_enqueues_shadow_turn_without_changing_visible_answer(tmp_path: Path) -> None:
    output = StringIO()
    database = tmp_path / "turn-understanding.sqlite"

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt="결과부터 알려줘",
            turn_understanding_db=database,
        ),
        stdout=output,
    )

    assert exit_code == 0
    assert output.getvalue() == "Companion> fake: 결과부터 알려줘\n"
    events = SqliteTurnUnderstandingRepository(database).list()
    assert len(events) == 1
    assert events[0].status == "pending"
    assert events[0].source == "cli"


def test_cli_analyzes_and_lists_pending_shadow_turns_with_explicit_fake(
    tmp_path: Path,
) -> None:
    database = tmp_path / "turn-understanding.sqlite"
    repository = SqliteTurnUnderstandingRepository(database)
    event = repository.enqueue(
        TurnInterpretationRequest("결과부터 알려줘"), source="web"
    )
    analyzed = StringIO()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            turn_understanding_db=database,
            analyze_pending_turns=True,
            analysis_limit=10,
        ),
        stdout=analyzed,
    )

    assert exit_code == 0
    assert analyzed.getvalue() == (
        "Shadow analysis: processed=1 completed=1 failed=0 deferred=0\n"
    )
    listed = StringIO()
    assert run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            turn_understanding_db=database,
            list_turn_understanding=True,
        ),
        stdout=listed,
    ) == 0
    text = listed.getvalue()
    assert event.id in text
    assert "completed source=web" in text
    assert '"intent_hypotheses": []' in text


def test_cli_links_persistent_turns_to_a_pending_outcome(tmp_path: Path) -> None:
    turns = tmp_path / "turns.sqlite"
    outcomes = tmp_path / "outcomes.sqlite"
    conversation = tmp_path / "conversation.sqlite"
    common = {
        "backend": "fake",
        "model_url": "http://unused",
        "conversation_db": conversation,
        "turn_understanding_db": turns,
        "outcome_db": outcomes,
    }

    first = StringIO()
    second = StringIO()
    assert run(Namespace(**common, prompt="첫 질문"), stdout=first) == 0
    assert run(
        Namespace(**common, prompt="아니, 그 뜻이 아니야"), stdout=second
    ) == 0

    assert first.getvalue() == "Companion> fake: 첫 질문\n"
    assert second.getvalue() == "Companion> fake: 아니, 그 뜻이 아니야\n"
    events = SqliteOutcomeRepository(outcomes).list()
    assert len(events) == 1
    assert events[0].assistant_response == "fake: 첫 질문"


def test_cli_analyzes_and_lists_outcomes_with_explicit_fake(
    tmp_path: Path,
) -> None:
    turns_path = tmp_path / "turns.sqlite"
    outcomes_path = tmp_path / "outcomes.sqlite"
    turns = SqliteTurnUnderstandingRepository(turns_path)
    prior = turns.enqueue(TurnInterpretationRequest("첫 질문"), source="cli")
    turns.complete(prior.id, FakeTurnInterpreter().interpret(prior.request))
    outcomes = SqliteOutcomeRepository(outcomes_path)
    outcomes.observe_turn(prior.id, source="cli", user_text="첫 질문")
    outcomes.record_response(prior.id, "첫 답변")
    current = turns.enqueue(TurnInterpretationRequest("맞아"), source="web")
    event = outcomes.observe_turn(current.id, source="web", user_text="맞아")
    assert event is not None
    analyzed = StringIO()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            turn_understanding_db=turns_path,
            outcome_db=outcomes_path,
            analyze_pending_outcomes=True,
            analysis_limit=10,
        ),
        stdout=analyzed,
    )

    assert exit_code == 0
    assert analyzed.getvalue() == (
        "Shadow outcomes: processed=1 completed=1 failed=0 deferred=0\n"
    )
    listed = StringIO()
    assert run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            turn_understanding_db=turns_path,
            outcome_db=outcomes_path,
            list_outcomes=True,
        ),
        stdout=listed,
    ) == 0
    assert event.id in listed.getvalue()
    assert "completed outcome=unclear" in listed.getvalue()


def test_cli_blind_review_shows_model_judgement_only_after_human_labels(
    tmp_path: Path,
) -> None:
    outcomes_path = tmp_path / "outcomes.sqlite"
    outcomes = SqliteOutcomeRepository(outcomes_path)
    outcomes.observe_turn("turn-1", source="cli", user_text="해결책을 알려줘")
    outcomes.record_response("turn-1", "많이 답답했겠네.")
    event = outcomes.observe_turn(
        "turn-2", source="cli", user_text="공감 말고 해결책을 원했어"
    )
    assert event is not None
    outcomes.complete(event.id, _completed_assessment(event.next_user_text))
    output = StringIO()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            outcome_db=outcomes_path,
            review_outcomes=True,
            review_limit=5,
            reviewer="cheonu",
        ),
        stdin=StringIO("2\n2\n2\n4\n1\n"),
        stdout=output,
    )

    assert exit_code == 0
    text = output.getvalue()
    assert text.index("천우의 질문") < text.index("겨울이의 사후 판단")
    assert "전체 반응: 천우=바로잡았다" in text
    assert "겨울이=바로잡았다" in text
    assert "이번에 1개 저장했습니다. 누적 검토는 1/30개입니다." in text
    assert len(outcomes.list_reviews(reviewer="cheonu")) == 1


def test_cli_outcome_audit_reports_progress_without_activating_it(
    tmp_path: Path,
) -> None:
    outcomes_path = tmp_path / "outcomes.sqlite"
    outcomes = SqliteOutcomeRepository(outcomes_path)
    outcomes.observe_turn("turn-1", source="cli", user_text="해결책을 알려줘")
    outcomes.record_response("turn-1", "많이 답답했겠네.")
    event = outcomes.observe_turn(
        "turn-2", source="cli", user_text="공감 말고 해결책을 원했어"
    )
    assert event is not None
    outcomes.complete(event.id, _completed_assessment(event.next_user_text))
    outcomes.add_review(
        event.id,
        reviewer="cheonu",
        outcome="corrected",
        intent_match="contradicted",
        response_usefulness="unhelpful",
        affect_shift="unclear",
        memory_relations=(),
        improvement_confirmation="confirmed",
    )
    output = StringIO()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            outcome_db=outcomes_path,
            outcome_audit=True,
            reviewer="cheonu",
        ),
        stdout=output,
    )

    assert exit_code == 0
    text = output.getvalue()
    assert "실제 대화 검토: 1/30" in text
    assert "아직 운영 반영을 판단하지 않습니다. 29개 검토가 더 필요합니다." in text
    assert "전체 반응: 1/1 (100.0%)" in text
    assert "자동 변경하지 않습니다" in text


def test_cli_reflect_history_recovers_raw_messages_without_inventing_fake_memory(
    tmp_path: Path,
) -> None:
    conversation_path = tmp_path / "conversations.sqlite"
    conversations = SqliteConversationRepository(conversation_path)
    conversations.append(ConversationMessage("user", "나는 SWM에서 일해"))
    conversations.append(ConversationMessage("assistant", "그렇구나"))
    output = StringIO()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            reflect_history=True,
            analysis_limit=10,
            conversation_db=conversation_path,
            memory_db=tmp_path / "memories.sqlite",
            turn_understanding_db=tmp_path / "turns.sqlite",
            outcome_db=tmp_path / "outcomes.sqlite",
            reflection_db=tmp_path / "reflection.sqlite",
        ),
        stdout=output,
    )

    assert exit_code == 0
    assert "대화 원문 연결: 천우 발화 1" in output.getvalue()
    assert "이번 기억 정리: 완료 1, 실패 0, 연결 보류 0" in output.getvalue()
    assert SqliteReflectionRepository(tmp_path / "reflection.sqlite").list() == ()
    assert SqliteMemoryRepository(tmp_path / "memories.sqlite").list_active() == ()


def test_cli_interactive_session_uses_one_core_for_turns() -> None:
    output = StringIO()

    exit_code = run(
        Namespace(backend="fake", model_url="http://unused", prompt=None),
        stdin=StringIO("첫 번째\n두 번째\n/exit\n"),
        stdout=output,
    )

    assert exit_code == 0
    assert "Companion> fake: 첫 번째\n" in output.getvalue()
    assert "Companion> fake: 두 번째\n" in output.getvalue()


def _completed_assessment(next_text: str) -> OutcomeAssessment:
    return OutcomeAssessment.from_dict(
        {
            "schema_version": 1,
            "outcome": "corrected",
            "confidence": 0.9,
            "evidence": [next_text],
            "intent_match": "contradicted",
            "response_usefulness": "unhelpful",
            "affect_shift": "unclear",
            "memory_confirmation": [],
            "memory_contradiction": [],
            "improvement_confirmation": [
                {
                    "content": "답변 방식 개선 필요",
                    "confidence": 0.9,
                    "evidence": [next_text],
                }
            ],
            "uncertainties": [],
        },
        evidence_text=next_text,
    )


def test_cli_persists_and_shows_history_when_database_is_explicit(tmp_path: Path) -> None:
    database_path = tmp_path / "conversation.sqlite"
    first_output = StringIO()
    run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt="기억될 대화",
            conversation_db=database_path,
            show_history=False,
        ),
        stdout=first_output,
    )
    history_output = StringIO()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            conversation_db=database_path,
            show_history=True,
        ),
        stdout=history_output,
    )

    assert first_output.getvalue() == "Companion> fake: 기억될 대화\n"
    assert exit_code == 0
    assert history_output.getvalue() == "user> 기억될 대화\nassistant> fake: 기억될 대화\n"


def test_cli_reports_unavailable_conversation_storage(tmp_path: Path) -> None:
    output = StringIO()
    database_path = tmp_path / "not-a-database"
    database_path.mkdir()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt="저장 실패",
            conversation_db=database_path,
            show_history=False,
        ),
        stdout=output,
    )

    assert exit_code == 1
    assert output.getvalue().startswith("Conversation storage unavailable:")


def test_cli_reports_explicit_memory_as_active(tmp_path: Path) -> None:
    output = StringIO()

    exit_code = run(
        Namespace(
            backend="fake", model_url="http://unused", prompt="기억해. Python config를 선호해",
            memory_db=tmp_path / "memories.sqlite",
        ),
        stdout=output,
    )

    assert exit_code == 0
    assert "Memory" in output.getvalue()
    assert "is active" in output.getvalue()
    assert "기억해둘게." in output.getvalue()


def test_cli_reports_automatic_direct_statement_capture(tmp_path: Path) -> None:
    output = StringIO()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt="나는 결론부터 듣는 설명을 좋아해",
            memory_db=tmp_path / "memories.sqlite",
        ),
        stdout=output,
    )

    assert exit_code == 0
    assert "captured from a direct statement" in output.getvalue()
    assert "기억해둘게." not in output.getvalue()


def test_cli_adds_lists_and_activates_an_evidence_backed_belief(tmp_path: Path) -> None:
    database_path = tmp_path / "beliefs.sqlite"
    added_output = StringIO()
    common = {
        "backend": "fake",
        "model_url": "http://unused",
        "prompt": None,
        "belief_db": database_path,
    }

    assert run(
        Namespace(
            **common,
            belief_add=("음성 개발 방향", "앞단 TTS 개선이 우선이다"),
            belief_rationale="질문 억양 실패가 반복됐다",
            belief_confidence=0.78,
            belief_evidence=("voice-test-22",),
        ),
        stdout=added_output,
    ) == 0
    belief = SqliteBeliefRepository(database_path).list()[0]
    assert belief.status == "candidate"
    assert "Belief" in added_output.getvalue()

    activated_output = StringIO()
    assert run(
        Namespace(**common, belief_activate=belief.id),
        stdout=activated_output,
    ) == 0
    assert SqliteBeliefRepository(database_path).get(belief.id).status == "active"

    listed_output = StringIO()
    assert run(
        Namespace(**common, list_beliefs=True),
        stdout=listed_output,
    ) == 0
    assert "앞단 TTS 개선이 우선이다" in listed_output.getvalue()
    assert "voice-test-22" in listed_output.getvalue()
