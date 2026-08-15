"""Local-LLM adapter for evidence-backed Shadow turn interpretation."""

from __future__ import annotations

from dataclasses import asdict
import json

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import ChatRequest, ConversationMessage
from companion.ports import ChatModel
from companion.turn_understanding import (
    MEMORY_KINDS,
    TEMPORAL_SCOPES,
    TurnInterpretationError,
    TurnInterpretationRequest,
    TurnInterpreterUnavailableError,
    TurnUnderstanding,
)

_SYSTEM_PROMPT = """너는 겨울이의 대화 이해를 사후 검토하는 분석기다.
현재 답변을 작성하지 말고, 아래 스키마의 JSON 객체 하나만 반환해.

원칙:
- 관찰과 추론을 분리하고, 확실하지 않으면 여러 가설과 uncertainty를 남겨.
- 사용자가 말하지 않은 사실·감정·선호는 추측하지 마.
- evidence에는 key 이름, 설명, 요약, 고친 문장을 쓰지 마. 입력 JSON의 실제 문장에서
  글자와 문장부호를 바꾸지 않은 연속 부분문자열을 그대로 복사해.
- temporal_scope는 문장 내용의 유효기간이야. 현재 기분·질문·모호한 반응은 turn/session,
  특정 사건과 나중 일정은 episodic, 직접 말한 지속 선호는 candidate_stable로 분류해.
- 현재 기분·질문·모호한 반응·제3자 사실은 사용자의 안정 기억으로 제안하지 마.
- "나는 ... 좋아해/싫어해"처럼 사용자가 직접 말한 지속 선호는 반드시 preference,
  candidate_stable memory proposal로 남겨. 간접 추론은 한 번으로 안정화하지 마.
- 사용자가 정한 작업 순서나 합의는 반드시 decision 또는 project memory proposal과
  project_signals에 남겨.
- 겨울이의 반복 실패나 능력 부족을 직접 지적하면 반드시 improvement_signals에 남겨.
- 나중에 다시 하자는 말은 반드시 open_loops에 남겨.
- memory_proposals는 제안일 뿐이며 자동 저장되지 않아.
- response schema에 없는 필드는 만들지 말고 모든 필수 필드를 포함해.
- 해당 사항이 없으면 배열은 빈 배열로 반환해.

분류 기준 예시(문장을 복사하지 말고 원칙만 적용):
- "나는 문서를 볼 때 예제를 먼저 보는 걸 좋아해"는 직접 지속 선호다.
  전체 temporal_scope와 preference proposal은 candidate_stable이다.
- "우선 데이터 정리를 끝내고 학습하기로 하자"는 프로젝트 결정이다.
  decision proposal과 project_signals를 남기고 turn으로 축소하지 않는다.
- 제안 뒤 "그 방향은 별로인 것 같아"만 왔다면 반대 가능성은 있지만 새 결정은 아니다.
  uncertainty를 남기고 memory proposal은 만들지 않는다.
- "네가 요청 조건을 반복해서 놓치는 게 문제야"는 겨울이의 반복 실패 피드백이다.
  improvement_signals에 남긴다."""

def _string_schema() -> dict[str, object]:
    return {"type": "string", "minLength": 1}


def _string_array_schema(*, min_items: int = 0) -> dict[str, object]:
    return {
        "type": "array",
        "items": _string_schema(),
        "minItems": min_items,
    }


def _object_schema(
    properties: dict[str, object], required: tuple[str, ...]
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _evidence_claim_schema() -> dict[str, object]:
    return _object_schema(
        {
            "content": _string_schema(),
            "evidence": _string_array_schema(min_items=1),
        },
        ("content", "evidence"),
    )


def _scored_schema() -> dict[str, object]:
    return _object_schema(
        {
            "label": _string_schema(),
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": _string_array_schema(min_items=1),
        },
        ("label", "confidence", "evidence"),
    )


def _response_format() -> dict[str, object]:
    temporal = {"type": "string", "enum": sorted(TEMPORAL_SCOPES)}
    memory = _object_schema(
        {
            "kind": {"type": "string", "enum": sorted(MEMORY_KINDS)},
            "content": _string_schema(),
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "temporal_scope": temporal,
            "evidence": _string_array_schema(min_items=1),
        },
        ("kind", "content", "confidence", "temporal_scope", "evidence"),
    )
    scored_array = {"type": "array", "items": _scored_schema()}
    root = _object_schema(
        {
            "schema_version": {"type": "integer", "const": 1},
            "literal_meaning": _string_schema(),
            "observations": {
                "type": "array",
                "items": _evidence_claim_schema(),
                "minItems": 1,
            },
            "intent_hypotheses": {
                "type": "array",
                "items": _scored_schema(),
                "minItems": 1,
            },
            "affect": scored_array,
            "conversational_need": _string_schema(),
            "temporal_scope": temporal,
            "response_contract": _object_schema(
                {
                    "must": _string_array_schema(),
                    "avoid": _string_array_schema(),
                },
                ("must", "avoid"),
            ),
            "memory_proposals": {"type": "array", "items": memory},
            "open_loops": scored_array,
            "project_signals": scored_array,
            "improvement_signals": scored_array,
            "uncertainties": _string_array_schema(),
        },
        (
            "schema_version",
            "literal_meaning",
            "observations",
            "intent_hypotheses",
            "affect",
            "conversational_need",
            "temporal_scope",
            "response_contract",
            "memory_proposals",
            "open_loops",
            "project_signals",
            "improvement_signals",
            "uncertainties",
        ),
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "turn_understanding",
            "strict": True,
            "schema": root,
        },
    }


class StructuredChatTurnInterpreter:
    """Uses a selected local ChatModel, then strictly validates its JSON."""

    def __init__(self, chat_model: ChatModel) -> None:
        self._chat_model = chat_model

    def interpret(self, request: TurnInterpretationRequest) -> TurnUnderstanding:
        payload: dict[str, object] = {
            "context": [asdict(message) for message in request.context],
            "current_user_text": request.user_text,
        }
        draft = self._generate(_SYSTEM_PROMPT, payload)
        return self._validate(draft, request)

    @staticmethod
    def _validate(
        payload: object, request: TurnInterpretationRequest
    ) -> TurnUnderstanding:
        understanding = TurnUnderstanding.from_dict(
            payload,
            source_text=request.user_text,
            context_texts=tuple(message.content for message in request.context),
        )
        user_evidence = (request.user_text,) + tuple(
            message.content
            for message in request.context
            if message.role == "user"
        )
        for proposal in understanding.memory_proposals:
            for quote in proposal.evidence:
                if not any(quote in text for text in user_evidence):
                    raise TurnInterpretationError(
                        "memory proposal evidence must come from a user message"
                    )
        return understanding

    def _generate(
        self, system_prompt: str, payload: dict[str, object]
    ) -> object:
        chat_request = ChatRequest(
            prompt=str(payload["current_user_text"]),
            messages=(
                ConversationMessage("system", system_prompt),
                ConversationMessage(
                    "user", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                ),
            ),
            max_tokens=1024,
            response_format=_response_format(),
            temperature=0.0,
            seed=42,
        )
        try:
            raw = self._chat_model.generate(chat_request).text
        except AdapterUnavailableError as error:
            raise TurnInterpreterUnavailableError(
                f"local turn interpreter unavailable: {error}"
            ) from error
        candidate = raw.strip()
        if candidate.startswith("```json\n") and candidate.endswith("\n```"):
            # Qwen sometimes obeys the JSON-only contract semantically but wraps
            # the single object in a Markdown fence. Accept exactly that wrapper,
            # never prose before/after it, then apply the same strict schema.
            candidate = candidate[len("```json\n") : -len("\n```")].strip()
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError as error:
            preview = " ".join(raw.strip().split())[:240] or "<empty>"
            raise TurnInterpretationError(
                "local turn interpreter did not return valid JSON: "
                f"{error.msg}; output={preview!r}"
            ) from error
        return decoded
