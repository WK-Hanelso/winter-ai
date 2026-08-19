import json

import pytest

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.reflection_interpreter import (
    StructuredChatReflectionInterpreter,
)
from companion.contracts import ChatRequest, ChatResult
from companion.reflection import (
    CurrentStateSnapshot,
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


def state_verification_payload(*, accept: bool) -> str:
    return json.dumps(
        {
            "about_user": accept,
            "matches_most_specific_topic": accept,
            "topic_grounded": accept,
            "current_condition": accept,
            "not_hypothetical_or_question": accept,
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
        ],
        "current_state_updates": [],
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
        ],
        "current_state_updates": [],
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
        ],
        "current_state_updates": [],
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
        ],
        "current_state_updates": [],
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
        ],
        "current_state_updates": [],
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


def test_compact_reflection_extracts_review_only_current_state_update() -> None:
    text = "요즘은 운동 덕분에 마음이 많이 괜찮아졌어"
    payload = {
        "proposals": [],
        "current_state_updates": [
            {
                "topic": "wellbeing",
                "confidence": 0.91,
            }
        ],
    }
    model = StubChatModel(
        json.dumps(payload, ensure_ascii=False),
        state_verification_payload(accept=True),
    )
    interpreter = StructuredChatReflectionInterpreter(model)  # type: ignore[arg-type]
    current = CurrentStateSnapshot(
        topic="wellbeing",
        memory_id="memory-1",
        content="요즘 마음이 힘들다",
    )

    result = interpreter.extract(
        ReflectionExtractionRequest(
            10,
            text,
            current_states=(current,),
            allow_current_state_updates=True,
        )
    )

    assert result.proposals == ()
    update = result.current_state_updates[0]
    assert update.topic == "wellbeing"
    assert update.content == text
    assert update.evidence == (text,)
    assert update.supersedes_memory_id == "memory-1"
    schema = model.requests[0].response_format["json_schema"]["schema"]  # type: ignore[index]
    updates = schema["properties"]["current_state_updates"]  # type: ignore[index]
    assert updates["maxItems"] == 3  # type: ignore[index]
    assert updates["items"]["properties"]["topic"]["enum"] == [  # type: ignore[index]
        "wellbeing"
    ]
    assert "evidence" not in updates["items"]["properties"]  # type: ignore[index]
    assert model.requests[1].seed == 44


def test_compact_reflection_rejects_state_update_when_verifier_is_uncertain() -> None:
    text = "내 마음이 괜찮아진 걸까?"
    payload = {
        "proposals": [],
        "current_state_updates": [
            {
                "topic": "wellbeing",
                "confidence": 0.6,
            }
        ],
    }
    current = CurrentStateSnapshot("wellbeing", "memory-1", "마음이 힘들다")
    model = StubChatModel(
        json.dumps(payload, ensure_ascii=False),
        state_verification_payload(accept=False),
    )

    result = StructuredChatReflectionInterpreter(model).extract(  # type: ignore[arg-type]
        ReflectionExtractionRequest(
            11,
            text,
            current_states=(current,),
            allow_current_state_updates=True,
        )
    )

    assert result.current_state_updates == ()


def test_compact_reflection_forbids_state_update_for_pre_cutoff_turn() -> None:
    text = "이제 마음이 괜찮아졌어"
    payload = {
        "proposals": [],
        "current_state_updates": [
            {
                "topic": "wellbeing",
                "confidence": 0.9,
            }
        ],
    }
    current = CurrentStateSnapshot("wellbeing", "memory-1", "마음이 힘들다")
    interpreter = StructuredChatReflectionInterpreter(  # type: ignore[arg-type]
        StubChatModel(json.dumps(payload, ensure_ascii=False))
    )

    with pytest.raises(ReflectionInterpretationError, match="updates are disabled"):
        interpreter.extract(
            ReflectionExtractionRequest(
                12,
                text,
                current_states=(current,),
                allow_current_state_updates=False,
            )
        )


def test_state_classification_suppresses_duplicate_long_term_candidate() -> None:
    text = "아직 마음은 조금 힘들어"
    payload = {
        "proposals": [
            {
                "kind": "semantic",
                "confidence": 0.9,
                "temporal_scope": "stable",
                "evidence": [text],
            }
        ],
        "current_state_updates": [
            {
                "topic": "wellbeing",
                "confidence": 0.9,
            }
        ],
    }
    model = StubChatModel(
        json.dumps(payload, ensure_ascii=False),
        state_verification_payload(accept=False),
    )
    current = CurrentStateSnapshot("wellbeing", "memory-1", "마음이 힘들다")

    result = StructuredChatReflectionInterpreter(model).extract(  # type: ignore[arg-type]
        ReflectionExtractionRequest(
            13,
            text,
            current_states=(current,),
            allow_current_state_updates=True,
        )
    )

    assert result.proposals == ()
    assert result.current_state_updates == ()
    assert len(model.requests) == 2
    schema = model.requests[1].response_format["json_schema"]  # type: ignore[index]
    assert schema["name"] == "current_state_update_verification"  # type: ignore[index]


def test_state_verifier_compares_every_active_topic() -> None:
    text = "회사 스트레스는 줄었어"
    payload = {
        "proposals": [],
        "current_state_updates": [
            {
                "topic": "work_learning_stress",
                "confidence": 0.9,
            }
        ],
    }
    model = StubChatModel(
        json.dumps(payload, ensure_ascii=False),
        state_verification_payload(accept=True),
    )
    states = (
        CurrentStateSnapshot("wellbeing", "memory-1", "마음이 힘들다"),
        CurrentStateSnapshot(
            "work_learning_stress",
            "memory-2",
            "회사와 공부 때문에 힘들다",
        ),
    )

    StructuredChatReflectionInterpreter(model).extract(  # type: ignore[arg-type]
        ReflectionExtractionRequest(
            14,
            text,
            current_states=states,
            allow_current_state_updates=True,
        )
    )

    verification_input = model.requests[1].messages[1].content
    assert '"topic":"wellbeing"' in verification_input
    assert '"topic":"work_learning_stress"' in verification_input


def test_state_update_rejects_deictic_reference_without_recent_context() -> None:
    text = "그건 이제 괜찮아"
    payload = {
        "proposals": [],
        "current_state_updates": [
            {"topic": "wellbeing", "confidence": 0.9},
            {"topic": "work_learning_stress", "confidence": 0.9},
        ],
    }
    model = StubChatModel(json.dumps(payload, ensure_ascii=False))
    states = (
        CurrentStateSnapshot("wellbeing", "memory-1", "마음이 힘들다"),
        CurrentStateSnapshot(
            "work_learning_stress",
            "memory-2",
            "회사와 공부 때문에 힘들다",
        ),
    )

    result = StructuredChatReflectionInterpreter(model).extract(  # type: ignore[arg-type]
        ReflectionExtractionRequest(
            15,
            text,
            current_states=states,
            allow_current_state_updates=True,
        )
    )

    assert result.current_state_updates == ()
    assert len(model.requests) == 1


def test_invalid_state_candidate_is_isolated_without_losing_valid_candidate() -> None:
    text = "마음은 나아졌는데 공부 부담은 여전해"
    payload = {
        "proposals": [],
        "current_state_updates": [
            {"topic": "wellbeing", "confidence": 0.9},
            {"topic": "work_learning_stress", "confidence": 1.7},
        ],
    }
    model = StubChatModel(
        json.dumps(payload, ensure_ascii=False),
        state_verification_payload(accept=True),
    )
    states = (
        CurrentStateSnapshot("wellbeing", "memory-1", "마음이 힘들다"),
        CurrentStateSnapshot(
            "work_learning_stress",
            "memory-2",
            "공부 부담이 크다",
        ),
    )

    result = StructuredChatReflectionInterpreter(model).extract(  # type: ignore[arg-type]
        ReflectionExtractionRequest(
            16,
            text,
            current_states=states,
            allow_current_state_updates=True,
        )
    )

    assert [update.topic for update in result.current_state_updates] == ["wellbeing"]
    assert result.warnings == (
        "current-state update 2 ignored: confidence must be between 0 and 1",
    )
    assert len(model.requests) == 2
