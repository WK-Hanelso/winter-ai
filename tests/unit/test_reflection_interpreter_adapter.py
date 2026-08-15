import json

import pytest

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.reflection_interpreter import (
    StructuredChatReflectionInterpreter,
)
from companion.contracts import ChatRequest, ChatResult
from companion.reflection import (
    ReflectionExtractionRequest,
    ReflectionInterpretationError,
    ReflectionInterpreterUnavailableError,
)


class StubChatModel:
    def __init__(self, *results: str | Exception) -> None:
        self._results = list(results)
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return ChatResult(result)


def verification_payload(*, accept: bool) -> str:
    return json.dumps(
        {
            "directly_stated": accept,
            "durable": accept,
            "not_momentary": accept,
            "accept": accept,
        }
    )


def test_compact_reflection_extracts_one_grounded_candidate() -> None:
    text = "나는 SWM에서 일하고 있어"
    payload = {
        "proposals": [
            {
                "kind": "semantic",
                "confidence": 0.95,
                "temporal_scope": "candidate_stable",
                "evidence": [text],
            }
        ]
    }
    model = StubChatModel(
        json.dumps(payload, ensure_ascii=False),
        verification_payload(accept=True),
    )
    interpreter = StructuredChatReflectionInterpreter(model)  # type: ignore[arg-type]

    result = interpreter.extract(ReflectionExtractionRequest(7, text))

    assert result.proposals[0].content == text
    request = model.requests[0]
    assert request.max_tokens == 256
    assert request.temperature == 0.0
    assert request.response_format is not None
    schema = request.response_format["json_schema"]
    assert isinstance(schema, dict)
    proposal = schema["schema"]["properties"]["proposals"]["items"]  # type: ignore[index]
    assert proposal["properties"]["evidence"]["items"]["const"] == text  # type: ignore[index]
    assert proposal["properties"]["kind"]["enum"] == [  # type: ignore[index]
        "decision",
        "preference",
        "procedural",
        "project",
        "semantic",
    ]
    assert proposal["properties"]["temporal_scope"]["enum"] == [  # type: ignore[index]
        "candidate_stable",
        "stable",
    ]
    assert schema["schema"]["properties"]["proposals"]["maxItems"] == 1  # type: ignore[index]
    verification_request = model.requests[1]
    assert verification_request.max_tokens == 96
    assert verification_request.temperature == 0.0
    assert verification_request.response_format is not None
    assert f'"content":"{text}"' in verification_request.messages[1].content


def test_compact_reflection_discards_candidate_rejected_by_verifier() -> None:
    text = "계속해줘"
    payload = {
        "proposals": [
            {
                "kind": "preference",
                "confidence": 0.95,
                "temporal_scope": "stable",
                "evidence": [text],
            }
        ]
    }
    model = StubChatModel(
        json.dumps(payload, ensure_ascii=False),
        verification_payload(accept=False),
    )

    result = StructuredChatReflectionInterpreter(model).extract(  # type: ignore[arg-type]
        ReflectionExtractionRequest(8, text)
    )

    assert result.proposals == ()


def test_compact_reflection_rejects_malformed_verification() -> None:
    text = "나는 SWM에서 일하고 있어"
    payload = {
        "proposals": [
            {
                "kind": "semantic",
                "confidence": 0.95,
                "temporal_scope": "candidate_stable",
                "evidence": [text],
            }
        ]
    }
    model = StubChatModel(json.dumps(payload, ensure_ascii=False), "not-json")

    with pytest.raises(ReflectionInterpretationError, match="verifier returned"):
        StructuredChatReflectionInterpreter(model).extract(  # type: ignore[arg-type]
            ReflectionExtractionRequest(9, text)
        )


def test_compact_reflection_rejects_evidence_not_copied_from_user() -> None:
    payload = {
        "proposals": [
            {
                "kind": "semantic",
                "confidence": 0.9,
                "temporal_scope": "candidate_stable",
                "evidence": ["바꿔 쓴 근거"],
            }
        ]
    }
    interpreter = StructuredChatReflectionInterpreter(  # type: ignore[arg-type]
        StubChatModel(json.dumps(payload, ensure_ascii=False))
    )

    with pytest.raises(ReflectionInterpretationError, match="exact current user"):
        interpreter.extract(ReflectionExtractionRequest(1, "나는 SWM에서 일해"))


def test_compact_reflection_rejects_non_long_term_candidate() -> None:
    text = "오늘은 조금 힘들어"
    payload = {
        "proposals": [
            {
                "kind": "relationship",
                "confidence": 0.9,
                "temporal_scope": "session",
                "evidence": [text],
            }
        ]
    }
    interpreter = StructuredChatReflectionInterpreter(  # type: ignore[arg-type]
        StubChatModel(json.dumps(payload, ensure_ascii=False))
    )

    with pytest.raises(ReflectionInterpretationError, match="unsupported reflection kind"):
        interpreter.extract(ReflectionExtractionRequest(1, text))


def test_compact_reflection_keeps_local_model_disconnect_retryable() -> None:
    interpreter = StructuredChatReflectionInterpreter(  # type: ignore[arg-type]
        StubChatModel(AdapterUnavailableError("tunnel reset"))
    )

    with pytest.raises(ReflectionInterpreterUnavailableError, match="tunnel reset"):
        interpreter.extract(ReflectionExtractionRequest(1, "나는 SWM에서 일해"))
