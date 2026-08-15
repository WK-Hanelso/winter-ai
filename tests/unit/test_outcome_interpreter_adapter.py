import json

import pytest

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.outcome_interpreter import StructuredChatOutcomeInterpreter
from companion.contracts import ChatRequest, ChatResult
from companion.outcome import (
    OutcomeInterpretationError,
    OutcomeInterpretationRequest,
    OutcomeMemoryClaim,
)
from companion.turn_understanding import (
    FakeTurnInterpreter,
    TurnInterpretationRequest,
)


class StubChatModel:
    def __init__(self, *results: str | Exception) -> None:
        self._results = results
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        result = self._results[len(self.requests) - 1]
        if isinstance(result, Exception):
            raise result
        return ChatResult(result)


def _request(*, with_memory: bool = True) -> OutcomeInterpretationRequest:
    prior = TurnInterpretationRequest("많이 답답해")
    return OutcomeInterpretationRequest(
        prior_user_text=prior.user_text,
        prior_understanding=FakeTurnInterpreter().interpret(prior),
        assistant_response="해결책부터 찾아볼게.",
        next_user_text="아니, 지금은 그냥 들어줬으면 했어.",
        memory_claims=(
            OutcomeMemoryClaim(
                "memory-1", "preference", "천우는 짧은 답을 선호한다."
            ),
        )
        if with_memory
        else (),
    )


def _categorical_payload() -> dict[str, object]:
    next_text = "아니, 지금은 그냥 들어줬으면 했어."
    return {
        "schema_version": 1,
        "outcome": "corrected",
        "confidence": 0.95,
        "evidence": [next_text],
        "intent_match": "contradicted",
        "response_usefulness": "unhelpful",
        "affect_shift": "unclear",
        "uncertainties": ["감정 변화는 명시되지 않았다"],
    }


def _memory_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "memory_relations": [
            {"claim_id": "memory-1", "relation": "none", "confidence": 0.9}
        ],
        "uncertainties": [],
    }


def test_structured_outcome_interpreter_uses_deterministic_json_schema() -> None:
    model = StubChatModel(
        json.dumps(_categorical_payload(), ensure_ascii=False),
        json.dumps(_memory_payload(), ensure_ascii=False),
    )
    interpreter = StructuredChatOutcomeInterpreter(model)  # type: ignore[arg-type]

    result = interpreter.evaluate(_request())

    assert result.outcome == "corrected"
    assert result.improvement_confirmation
    assert len(model.requests) == 2
    categorical, signals = model.requests
    assert all(sent.temperature == 0.0 for sent in model.requests)
    assert all(sent.seed == 42 for sent in model.requests)
    assert all(sent.response_format is not None for sent in model.requests)
    assert "단순 주제 전환" in categorical.messages[0].content
    assert "intent_match는 assistant_response" in categorical.messages[0].content
    assert "실제로 사용된 장기 Memory claim" in signals.messages[0].content
    request_payload = json.loads(categorical.messages[1].content)
    assert request_payload["assistant_response"] == "해결책부터 찾아볼게."
    assert request_payload["next_user_text"] == "아니, 지금은 그냥 들어줬으면 했어."
    response_schema = categorical.response_format["json_schema"]["schema"]
    evidence_items = response_schema["properties"]["evidence"]["items"]
    assert evidence_items["const"] == request_payload["next_user_text"]
    signal_payload = json.loads(signals.messages[1].content)
    assert signal_payload["memory_claims"][0]["id"] == "memory-1"
    signal_name = signals.response_format["json_schema"]["name"]
    assert signal_name == "outcome_memory_relations"


def test_structured_outcome_interpreter_rejects_invalid_evidence() -> None:
    payload = _categorical_payload()
    payload["evidence"] = ["요약한 정정"]
    interpreter = StructuredChatOutcomeInterpreter(  # type: ignore[arg-type]
        StubChatModel(json.dumps(payload, ensure_ascii=False))
    )

    with pytest.raises(OutcomeInterpretationError, match="exact substring"):
        interpreter.evaluate(_request())


def test_structured_outcome_interpreter_does_not_hide_model_failure() -> None:
    interpreter = StructuredChatOutcomeInterpreter(  # type: ignore[arg-type]
        StubChatModel(AdapterUnavailableError("Orin unavailable"))
    )

    with pytest.raises(OutcomeInterpretationError, match="Orin unavailable"):
        interpreter.evaluate(_request())


def test_structured_outcome_interpreter_does_not_persist_partial_stage() -> None:
    interpreter = StructuredChatOutcomeInterpreter(  # type: ignore[arg-type]
        StubChatModel(
            json.dumps(_categorical_payload(), ensure_ascii=False),
            AdapterUnavailableError("signal unavailable"),
        )
    )

    with pytest.raises(
        OutcomeInterpretationError, match="memory interpreter unavailable"
    ):
        interpreter.evaluate(_request())


def test_positive_categorical_result_rejects_improvement_contradiction() -> None:
    categorical = _categorical_payload()
    categorical.update(
        {
            "outcome": "accepted",
            "intent_match": "confirmed",
            "response_usefulness": "helpful",
            "affect_shift": "improved",
        }
    )
    interpreter = StructuredChatOutcomeInterpreter(  # type: ignore[arg-type]
        StubChatModel(
            json.dumps(categorical, ensure_ascii=False),
            json.dumps(_memory_payload(), ensure_ascii=False),
        )
    )

    result = interpreter.evaluate(_request())

    assert not result.improvement_confirmation


def test_memory_stage_requires_every_provenance_id_once() -> None:
    signals = _memory_payload()
    signals["memory_relations"] = []
    interpreter = StructuredChatOutcomeInterpreter(  # type: ignore[arg-type]
        StubChatModel(
            json.dumps(_categorical_payload(), ensure_ascii=False),
            json.dumps(signals, ensure_ascii=False),
        )
    )

    with pytest.raises(OutcomeInterpretationError, match="every claim id"):
        interpreter.evaluate(_request())


def test_memory_stage_is_skipped_without_core_provenance() -> None:
    model = StubChatModel(
        json.dumps(_categorical_payload(), ensure_ascii=False)
    )
    interpreter = StructuredChatOutcomeInterpreter(model)  # type: ignore[arg-type]

    result = interpreter.evaluate(_request(with_memory=False))

    assert len(model.requests) == 1
    assert not result.memory_confirmation
    assert not result.memory_contradiction
    assert result.improvement_confirmation
