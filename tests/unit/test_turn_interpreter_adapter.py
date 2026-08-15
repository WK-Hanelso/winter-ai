import json

import pytest

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.turn_interpreter import StructuredChatTurnInterpreter
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.turn_understanding import (
    TurnInterpretationError,
    TurnInterpretationRequest,
)


class StubChatModel:
    def __init__(self, result: str | Exception) -> None:
        self._result = result
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        if isinstance(self._result, Exception):
            raise self._result
        return ChatResult(self._result)


def _valid_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "literal_meaning": "결과를 먼저 보는 설명이 편했다는 말이다.",
        "observations": [
            {
                "content": "결과 우선 설명이 더 편했다고 직접 말했다.",
                "evidence": ["결과부터 보여주는 쪽이 편했어"],
            }
        ],
        "intent_hypotheses": [
            {
                "label": "설명 순서 선호를 전달한다",
                "confidence": 0.86,
                "evidence": ["결과부터 보여주는 쪽이 편했어"],
            }
        ],
        "affect": [],
        "conversational_need": "acknowledge_preference",
        "temporal_scope": "candidate_stable",
        "response_contract": {
            "must": ["관찰된 선호를 짧게 확인한다"],
            "avoid": ["안정적인 영구 선호라고 단정한다"],
        },
        "memory_proposals": [],
        "open_loops": [],
        "project_signals": [],
        "improvement_signals": [],
        "uncertainties": ["다른 설명 상황에도 적용되는지는 모른다"],
    }


def test_structured_interpreter_requests_json_and_validates_exact_evidence() -> None:
    model = StubChatModel(json.dumps(_valid_payload(), ensure_ascii=False))
    interpreter = StructuredChatTurnInterpreter(model)  # type: ignore[arg-type]
    request = TurnInterpretationRequest(
        "결과부터 보여주는 쪽이 편했어",
        (ConversationMessage("assistant", "두 가지 방식으로 설명해봤어."),),
    )

    result = interpreter.interpret(request)

    assert result.intent_hypotheses[0].confidence == 0.86
    sent = model.requests[0]
    assert sent.max_tokens == 1024
    assert sent.temperature == 0.0
    assert sent.seed == 42
    assert sent.response_format is not None
    schema = sent.response_format["json_schema"]
    assert isinstance(schema, dict)
    assert schema["strict"] is True
    assert "JSON 객체 하나만" in sent.messages[0].content
    assert "추측하지" in sent.messages[0].content
    payload = json.loads(sent.messages[1].content)
    assert payload["current_user_text"] == request.user_text
    assert payload["context"][0]["role"] == "assistant"
    assert len(model.requests) == 1


def test_structured_interpreter_accepts_one_json_code_fence_but_no_prose() -> None:
    payload = json.dumps(_valid_payload(), ensure_ascii=False)
    fenced = StructuredChatTurnInterpreter(  # type: ignore[arg-type]
        StubChatModel(f"```json\n{payload}\n```")
    )

    assert fenced.interpret(
        TurnInterpretationRequest("결과부터 보여주는 쪽이 편했어")
    ).schema_version == 1

    prose = StructuredChatTurnInterpreter(  # type: ignore[arg-type]
        StubChatModel(f"분석 결과입니다.\n```json\n{payload}\n```")
    )
    with pytest.raises(TurnInterpretationError, match="valid JSON"):
        prose.interpret(
            TurnInterpretationRequest("결과부터 보여주는 쪽이 편했어")
        )


@pytest.mark.parametrize(
    "output, expected",
    [
        ("설명문만 반환", "valid JSON"),
        (json.dumps({"schema_version": 1}), "fields mismatch"),
    ],
)
def test_structured_interpreter_rejects_malformed_output(
    output: str, expected: str
) -> None:
    interpreter = StructuredChatTurnInterpreter(StubChatModel(output))  # type: ignore[arg-type]

    with pytest.raises(TurnInterpretationError, match=expected):
        interpreter.interpret(TurnInterpretationRequest("안녕"))


def test_structured_interpreter_reports_local_model_failure_without_fallback() -> None:
    interpreter = StructuredChatTurnInterpreter(
        StubChatModel(AdapterUnavailableError("Orin unavailable"))  # type: ignore[arg-type]
    )

    with pytest.raises(TurnInterpretationError, match="Orin unavailable"):
        interpreter.interpret(TurnInterpretationRequest("안녕"))


def test_memory_proposal_cannot_use_assistant_words_as_user_evidence() -> None:
    payload = _valid_payload()
    payload["observations"] = [
        {"content": "사용자가 동의했다.", "evidence": ["응, 맞아"]}
    ]
    payload["intent_hypotheses"] = [
        {
            "label": "이전 말에 동의한다",
            "confidence": 0.8,
            "evidence": ["응, 맞아"],
        }
    ]
    payload["memory_proposals"] = [
        {
            "kind": "semantic",
            "content": "천우는 SWM에서 일한다.",
            "confidence": 0.9,
            "temporal_scope": "candidate_stable",
            "evidence": ["천우는 SWM에서 일해"],
        }
    ]
    interpreter = StructuredChatTurnInterpreter(  # type: ignore[arg-type]
        StubChatModel(json.dumps(payload, ensure_ascii=False))
    )
    request = TurnInterpretationRequest(
        "응, 맞아",
        (ConversationMessage("assistant", "천우는 SWM에서 일해"),),
    )

    with pytest.raises(TurnInterpretationError, match="user message"):
        interpreter.interpret(request)
