from pathlib import Path

import pytest

from companion.contracts import ConversationMessage
from companion.turn_understanding import (
    FakeTurnInterpreter,
    ShadowAnalysisService,
    SqliteTurnUnderstandingRepository,
    TurnInterpretationError,
    TurnInterpretationRequest,
    TurnInterpreterUnavailableError,
    TurnUnderstanding,
    TurnUnderstandingRepositoryError,
)


def _request(text: str = "결과부터 보여주는 쪽이 편했어") -> TurnInterpretationRequest:
    return TurnInterpretationRequest(
        user_text=text,
        context=(
            ConversationMessage("assistant", "두 가지 방식으로 설명해봤어."),
            ConversationMessage("user", text),
        ),
    )


def _understanding(text: str) -> TurnUnderstanding:
    return TurnUnderstanding.from_dict(
        {
            "schema_version": 1,
            "literal_meaning": "결과를 먼저 보는 설명이 더 편했다는 말이다.",
            "observations": [
                {
                    "content": "결과 우선 설명이 더 편했다고 직접 말했다.",
                    "evidence": ["결과부터 보여주는 쪽이 편했어"],
                }
            ],
            "intent_hypotheses": [
                {
                    "label": "설명 순서 선호를 전달한다",
                    "confidence": 0.88,
                    "evidence": ["결과부터 보여주는 쪽이 편했어"],
                }
            ],
            "affect": [],
            "conversational_need": "acknowledge_preference",
            "temporal_scope": "candidate_stable",
            "response_contract": {
                "must": ["관찰된 선호를 짧게 확인한다"],
                "avoid": ["모든 상황의 안정 선호로 단정한다"],
            },
            "memory_proposals": [
                {
                    "kind": "preference",
                    "content": "천우는 결과를 먼저 보는 설명을 선호할 수 있다.",
                    "confidence": 0.72,
                    "temporal_scope": "candidate_stable",
                    "evidence": ["결과부터 보여주는 쪽이 편했어"],
                }
            ],
            "open_loops": [],
            "project_signals": [],
            "improvement_signals": [],
            "uncertainties": ["모든 설명 상황에 적용되는지는 아직 모른다"],
        },
        source_text=text,
    )


def test_schema_rejects_inference_without_exact_source_evidence() -> None:
    with pytest.raises(TurnInterpretationError, match="exact substring"):
        TurnUnderstanding.from_dict(
            {
                "schema_version": 1,
                "literal_meaning": "선호 표현",
                "observations": [],
                "intent_hypotheses": [
                    {
                        "label": "설명 선호",
                        "confidence": 0.8,
                        "evidence": ["원문에 없는 근거"],
                    }
                ],
                "affect": [],
                "conversational_need": "acknowledge_preference",
                "temporal_scope": "candidate_stable",
                "response_contract": {"must": [], "avoid": []},
                "memory_proposals": [],
                "open_loops": [],
                "project_signals": [],
                "improvement_signals": [],
                "uncertainties": [],
            },
            source_text="결과부터 말해줘",
        )


def test_schema_rejects_invalid_confidence_and_temporal_scope() -> None:
    payload = _understanding("결과부터 보여주는 쪽이 편했어").to_dict()
    payload["intent_hypotheses"][0]["confidence"] = 1.2
    with pytest.raises(TurnInterpretationError, match="confidence"):
        TurnUnderstanding.from_dict(payload, source_text="결과부터 보여주는 쪽이 편했어")

    payload = _understanding("결과부터 보여주는 쪽이 편했어").to_dict()
    payload["temporal_scope"] = "forever"
    with pytest.raises(TurnInterpretationError, match="temporal_scope"):
        TurnUnderstanding.from_dict(payload, source_text="결과부터 보여주는 쪽이 편했어")


def test_fake_interpreter_is_deterministic_and_does_not_infer() -> None:
    request = _request()

    first = FakeTurnInterpreter().interpret(request)
    second = FakeTurnInterpreter().interpret(request)

    assert first == second
    assert first.literal_meaning == request.user_text
    assert first.intent_hypotheses == ()
    assert first.memory_proposals == ()
    assert "does not infer" in first.uncertainties[0]


def test_sqlite_repository_persists_pending_and_completed_shadow_events(
    tmp_path: Path,
) -> None:
    database = tmp_path / "turn-understanding.sqlite"
    repository = SqliteTurnUnderstandingRepository(database)
    pending = repository.enqueue(_request(), source="cli")

    reopened = SqliteTurnUnderstandingRepository(database)
    assert reopened.get(pending.id).status == "pending"
    completed = reopened.complete(
        pending.id, _understanding("결과부터 보여주는 쪽이 편했어")
    )

    assert completed.status == "completed"
    assert completed.understanding is not None
    assert completed.understanding.memory_proposals[0].kind == "preference"
    assert SqliteTurnUnderstandingRepository(database).list()[0] == completed


def test_repository_rejects_newer_schema_version(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "turn-understanding.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE turn_understanding_schema (version INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO turn_understanding_schema VALUES (999)")

    with pytest.raises(TurnUnderstandingRepositoryError, match="unsupported schema"):
        SqliteTurnUnderstandingRepository(database)


def test_shadow_service_records_failure_without_fake_fallback(tmp_path: Path) -> None:
    class FailingInterpreter:
        def interpret(self, request: TurnInterpretationRequest) -> TurnUnderstanding:
            raise TurnInterpretationError("malformed local model output")

    repository = SqliteTurnUnderstandingRepository(tmp_path / "turn.sqlite")
    event = repository.enqueue(_request(), source="voice")

    result = ShadowAnalysisService(repository, FailingInterpreter()).process_pending()

    assert (result.processed, result.completed, result.failed) == (1, 0, 1)
    failed = repository.get(event.id)
    assert failed.status == "failed"
    assert failed.error == "malformed local model output"


def test_shadow_service_completes_pending_events_with_fake_interpreter(
    tmp_path: Path,
) -> None:
    repository = SqliteTurnUnderstandingRepository(tmp_path / "turn.sqlite")
    repository.enqueue(_request("첫 문장"), source="cli")
    repository.enqueue(_request("둘째 문장"), source="web")

    result = ShadowAnalysisService(repository, FakeTurnInterpreter()).process_pending(limit=1)

    assert (result.processed, result.completed, result.failed) == (1, 1, 0)
    assert [event.status for event in repository.list()] == ["completed", "pending"]


def test_shadow_service_keeps_transient_model_failure_pending(tmp_path: Path) -> None:
    class UnavailableInterpreter:
        def interpret(self, request: TurnInterpretationRequest) -> TurnUnderstanding:
            raise TurnInterpreterUnavailableError("tunnel reconnected")

    repository = SqliteTurnUnderstandingRepository(tmp_path / "turn.sqlite")
    event = repository.enqueue(_request(), source="cli")

    result = ShadowAnalysisService(repository, UnavailableInterpreter()).process_pending()

    assert (result.processed, result.completed, result.failed, result.deferred) == (
        1,
        0,
        0,
        1,
    )
    assert repository.get(event.id).status == "pending"
