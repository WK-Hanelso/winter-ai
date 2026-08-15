from pathlib import Path
import sqlite3

import pytest

from companion.contracts import ConversationMessage
from companion.outcome import (
    FakeOutcomeInterpreter,
    OutcomeAssessment,
    OutcomeInterpretationError,
    OutcomeInterpretationRequest,
    OutcomeInterpreterUnavailableError,
    OutcomeMemoryClaim,
    OutcomeRepositoryError,
    ReviewedMemoryRelation,
    ShadowOutcomeService,
    SqliteOutcomeRepository,
)
from companion.outcome_review import build_audit
from companion.turn_understanding import (
    FakeTurnInterpreter,
    SqliteTurnUnderstandingRepository,
    TurnInterpretationRequest,
)


def _assessment(next_text: str) -> OutcomeAssessment:
    return OutcomeAssessment.from_dict(
        {
            "schema_version": 1,
            "outcome": "corrected",
            "confidence": 0.94,
            "evidence": [next_text],
            "intent_match": "contradicted",
            "response_usefulness": "unhelpful",
            "affect_shift": "unclear",
            "memory_confirmation": [],
            "memory_contradiction": [],
            "improvement_confirmation": [
                {
                    "content": "이전 답변이 요청 의도를 놓쳤다.",
                    "confidence": 0.9,
                    "evidence": [next_text],
                }
            ],
            "uncertainties": ["감정 변화는 직접 확인되지 않았다"],
        },
        evidence_text=next_text,
    )


def test_outcome_schema_requires_exact_next_turn_evidence() -> None:
    payload = _assessment("아니, 해결 방법을 물어본 거야").to_dict()
    payload["evidence"] = ["원문에 없는 정정"]

    with pytest.raises(OutcomeInterpretationError, match="exact substring"):
        OutcomeAssessment.from_dict(
            payload, evidence_text="아니, 해결 방법을 물어본 거야"
        )


def test_outcome_schema_rejects_unsupported_state_and_confidence() -> None:
    payload = _assessment("맞아").to_dict()
    payload["outcome"] = "silently_assumed"
    with pytest.raises(OutcomeInterpretationError, match="outcome"):
        OutcomeAssessment.from_dict(payload, evidence_text="맞아")

    payload = _assessment("맞아").to_dict()
    payload["confidence"] = 1.2
    with pytest.raises(OutcomeInterpretationError, match="confidence"):
        OutcomeAssessment.from_dict(payload, evidence_text="맞아")


def test_fake_outcome_interpreter_is_unclear_and_deterministic() -> None:
    request = _outcome_request("응, 계속해")

    first = FakeOutcomeInterpreter().evaluate(request)
    second = FakeOutcomeInterpreter().evaluate(request)

    assert first == second
    assert first.outcome == "unclear"
    assert first.intent_match == "unclear"
    assert first.memory_confirmation == ()


def test_repository_links_only_a_responded_turn_to_the_next_turn(
    tmp_path: Path,
) -> None:
    repository = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    claim = OutcomeMemoryClaim("memory-1", "preference", "천우는 결론을 선호한다")
    assert (
        repository.observe_turn(
            "turn-1",
            source="cli",
            user_text="첫 질문",
            memory_claims=(claim,),
        )
        is None
    )
    repository.record_response("turn-1", "첫 답변")

    linked = repository.observe_turn(
        "turn-2", source="web", user_text="아니, 그 뜻이 아니야"
    )

    assert linked is not None
    assert linked.prior_turn_id == "turn-1"
    assert linked.next_turn_id == "turn-2"
    assert linked.prior_user_text == "첫 질문"
    assert linked.assistant_response == "첫 답변"
    assert linked.next_user_text == "아니, 그 뜻이 아니야"
    assert linked.prior_memory_claims == (claim,)
    assert linked.status == "pending"
    assert SqliteOutcomeRepository(tmp_path / "outcomes.sqlite").get(linked.id) == linked


def test_repository_migrates_v1_turns_with_empty_memory_provenance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outcomes.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE outcome_schema (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO outcome_schema VALUES (1)")
        connection.execute(
            """
            CREATE TABLE outcome_turns (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                user_text TEXT NOT NULL,
                response_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    repository = SqliteOutcomeRepository(path)
    repository.observe_turn("turn-1", source="cli", user_text="질문")
    repository.record_response("turn-1", "답변")
    linked = repository.observe_turn("turn-2", source="cli", user_text="반응")

    assert linked is not None
    assert linked.prior_memory_claims == ()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM outcome_schema"
        ).fetchone() == (3,)


def test_repository_saves_one_blind_review_and_hides_it_from_review_queue(
    tmp_path: Path,
) -> None:
    repository = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    claim = OutcomeMemoryClaim("memory-1", "preference", "천우는 결론을 선호한다")
    repository.observe_turn(
        "turn-1",
        source="cli",
        user_text="첫 질문",
        memory_claims=(claim,),
    )
    repository.record_response("turn-1", "첫 답변")
    event = repository.observe_turn("turn-2", source="cli", user_text="아니, 다시 해줘")
    assert event is not None
    repository.complete(event.id, _assessment(event.next_user_text))

    assert repository.list_reviewable(reviewer="cheonu") == (repository.get(event.id),)
    review = repository.add_review(
        event.id,
        reviewer="cheonu",
        outcome="corrected",
        intent_match="contradicted",
        response_usefulness="unhelpful",
        affect_shift="unclear",
        memory_relations=(ReviewedMemoryRelation(claim.id, "none"),),
        improvement_confirmation="confirmed",
    )

    assert repository.list_reviews(reviewer="cheonu") == (review,)
    assert repository.list_reviewable(reviewer="cheonu") == ()
    assert repository.list_reviewable(reviewer="second-reviewer") == (
        repository.get(event.id),
    )


def test_repository_requires_a_review_for_every_memory_used_in_the_answer(
    tmp_path: Path,
) -> None:
    repository = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    claim = OutcomeMemoryClaim("memory-1", "preference", "천우는 결론을 선호한다")
    repository.observe_turn(
        "turn-1", source="cli", user_text="질문", memory_claims=(claim,)
    )
    repository.record_response("turn-1", "답변")
    event = repository.observe_turn("turn-2", source="cli", user_text="반응")
    assert event is not None
    repository.complete(event.id, _assessment(event.next_user_text))

    with pytest.raises(OutcomeRepositoryError, match="every used memory"):
        repository.add_review(
            event.id,
            reviewer="cheonu",
            outcome="unclear",
            intent_match="unclear",
            response_usefulness="unclear",
            affect_shift="unclear",
            memory_relations=(),
            improvement_confirmation="unclear",
        )


def test_audit_counts_a_reversed_memory_relation_as_false_and_missed(
    tmp_path: Path,
) -> None:
    repository = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    claim = OutcomeMemoryClaim("memory-1", "preference", "천우는 결론을 선호한다")
    repository.observe_turn(
        "turn-1", source="cli", user_text="질문", memory_claims=(claim,)
    )
    repository.record_response("turn-1", "천우는 결론을 선호하니까 결론부터 말할게")
    next_text = "아니, 설명부터 듣는 게 좋아"
    event = repository.observe_turn("turn-2", source="cli", user_text=next_text)
    assert event is not None
    payload = _assessment(next_text).to_dict()
    payload["memory_confirmation"] = [
        {
            "content": claim.content,
            "confidence": 0.9,
            "evidence": [next_text],
        }
    ]
    repository.complete(
        event.id,
        OutcomeAssessment.from_dict(payload, evidence_text=next_text),
    )
    repository.add_review(
        event.id,
        reviewer="cheonu",
        outcome="corrected",
        intent_match="contradicted",
        response_usefulness="unhelpful",
        affect_shift="unclear",
        memory_relations=(ReviewedMemoryRelation(claim.id, "contradicted"),),
        improvement_confirmation="confirmed",
    )

    score = build_audit(repository, reviewer="cheonu").memory_detection

    assert score.true_positive == 0
    assert score.false_positive == 1
    assert score.false_negative == 1


def test_repository_does_not_assume_an_outcome_without_a_response(
    tmp_path: Path,
) -> None:
    repository = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    repository.observe_turn("turn-1", source="cli", user_text="첫 질문")

    assert (
        repository.observe_turn("turn-2", source="cli", user_text="다음 질문")
        is None
    )
    assert repository.list() == ()


def test_repository_creates_at_most_one_outcome_for_a_prior_turn(
    tmp_path: Path,
) -> None:
    repository = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    repository.observe_turn("turn-1", source="cli", user_text="첫 질문")
    repository.record_response("turn-1", "첫 답변")
    assert repository.observe_turn("turn-2", source="cli", user_text="둘째 말")
    repository.record_response("turn-2", "둘째 답변")
    assert repository.observe_turn("turn-3", source="voice", user_text="셋째 말")

    events = repository.list()

    assert [(event.prior_turn_id, event.next_turn_id) for event in events] == [
        ("turn-1", "turn-2"),
        ("turn-2", "turn-3"),
    ]


def test_shadow_service_defers_until_prior_understanding_is_completed(
    tmp_path: Path,
) -> None:
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    prior = turns.enqueue(TurnInterpretationRequest("첫 질문"), source="cli")
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    outcomes.observe_turn(prior.id, source="cli", user_text="첫 질문")
    outcomes.record_response(prior.id, "첫 답변")
    current = turns.enqueue(TurnInterpretationRequest("맞아"), source="cli")
    outcomes.observe_turn(current.id, source="cli", user_text="맞아")

    result = ShadowOutcomeService(
        outcomes, turns, FakeOutcomeInterpreter()
    ).process_pending()

    assert (result.processed, result.completed, result.failed, result.deferred) == (
        1,
        0,
        0,
        1,
    )
    assert outcomes.list()[0].status == "pending"


def test_shadow_service_completes_after_prior_understanding_exists(
    tmp_path: Path,
) -> None:
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    prior = turns.enqueue(TurnInterpretationRequest("첫 질문"), source="cli")
    turns.complete(prior.id, FakeTurnInterpreter().interpret(prior.request))
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    outcomes.observe_turn(prior.id, source="cli", user_text="첫 질문")
    outcomes.record_response(prior.id, "첫 답변")
    current = turns.enqueue(TurnInterpretationRequest("맞아"), source="cli")
    linked = outcomes.observe_turn(current.id, source="cli", user_text="맞아")
    assert linked is not None

    result = ShadowOutcomeService(
        outcomes, turns, FakeOutcomeInterpreter()
    ).process_pending()

    assert (result.processed, result.completed, result.failed, result.deferred) == (
        1,
        1,
        0,
        0,
    )
    completed = outcomes.get(linked.id)
    assert completed.status == "completed"
    assert completed.assessment is not None
    assert completed.assessment.outcome == "unclear"


def test_shadow_service_keeps_transient_outcome_failure_pending(
    tmp_path: Path,
) -> None:
    class UnavailableInterpreter:
        def evaluate(self, request: OutcomeInterpretationRequest) -> OutcomeAssessment:
            raise OutcomeInterpreterUnavailableError("tunnel reconnected")

    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    prior = turns.enqueue(TurnInterpretationRequest("첫 질문"), source="cli")
    turns.complete(prior.id, FakeTurnInterpreter().interpret(prior.request))
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    outcomes.observe_turn(prior.id, source="cli", user_text="첫 질문")
    outcomes.record_response(prior.id, "첫 답변")
    current = turns.enqueue(TurnInterpretationRequest("다음 반응"), source="cli")
    event = outcomes.observe_turn(current.id, source="cli", user_text="다음 반응")
    assert event is not None

    result = ShadowOutcomeService(
        outcomes, turns, UnavailableInterpreter()
    ).process_pending()

    assert (result.processed, result.completed, result.failed, result.deferred) == (
        1,
        0,
        0,
        1,
    )
    assert outcomes.get(event.id).status == "pending"


def _outcome_request(next_text: str) -> OutcomeInterpretationRequest:
    prior_request = TurnInterpretationRequest(
        "많이 답답했겠네. 해결책보다 먼저 들어줄게",
        (ConversationMessage("assistant", "이전 문맥"),),
    )
    understanding = FakeTurnInterpreter().interpret(prior_request)
    return OutcomeInterpretationRequest(
        prior_user_text=prior_request.user_text,
        prior_understanding=understanding,
        assistant_response="많이 답답했겠네.",
        next_user_text=next_text,
    )
