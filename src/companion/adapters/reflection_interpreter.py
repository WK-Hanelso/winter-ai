"""Compact local-LLM extraction of review-only conversational memories."""

from __future__ import annotations

import json

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import ChatRequest, ConversationMessage
from companion.ports import ChatModel
from companion.reflection import (
    ReflectionExtraction,
    ReflectionExtractionRequest,
    ReflectionInterpretationError,
    ReflectionInterpreterUnavailableError,
)
from companion.turn_understanding import MemoryProposal

_SYSTEM_PROMPT = """너는 천우와 겨울이의 과거 대화에서 장기 기억 후보만 찾는다.
답변하지 말고 JSON 객체 하나만 반환해.

- current_user_text는 천우가 실제로 말한 현재 문장이고 recent_context는 직전 대화다.
- recent_context는 의도 구분에만 쓰며 그 안의 정보를 현재 문장의 후보로 만들지 않는다.
- 다음 주의 전혀 다른 대화에서도 겨울이의 답변을 바꿀 안정적인 정보만 후보로 만든다.
- 허용 종류는 semantic(사용자 사실), preference(지속 선호), decision(지속되는 결정),
  project(실제 업무·개발 목표), procedural(겨울이가 사용자를 돕는 지속 방식)뿐이다.
- 겨울이가 앞으로 더 질문하거나 특정 방식으로 대화하길 바란다는 말은 preference다.
- 특정 시점의 경험·감정·질문·인사·짧은 동의·추측은 원문 대화에만 남기고 후보로
  만들지 않는다.
- 질문에 나온 기술·대상은 천우의 사실, 선호, 프로젝트라고 추론하지 않는다.
- 운동이나 자기관리는 사용자가 명시적으로 장기 목표라고 말하지 않으면 project가 아니다.
- 같은 의미를 여러 종류로 중복 제안하지 않는다.
- 기억 본문은 시스템이 실제 원문으로 저장한다. 별도 요약문을 만들지 않는다.
- evidence에는 입력의 current_user_text 전체를 글자 하나 바꾸지 않고 넣는다.
- 한 문장에서 미래에 가장 유용한 후보 하나만 제안하고, 없으면 빈 배열을 반환한다.
- 이 결과는 자동 기억이 아니라 사용자가 검토할 후보일 뿐이다.

판단 예시:
- '계속해줘' → 후보 없음
- '좋은 E2E 아키텍처가 뭐야?' → 후보 없음. 질문의 주제를 프로젝트로 만들지 않음
- '오늘 힘들어서 운동했어' → 후보 없음. 순간 경험임
- '나는 A회사에서 일해' → semantic 1개
- '나는 B 기술을 적용하는 프로젝트를 진행해' → project 1개
- '정보를 읽듯 전하지 말고 나를 이해시키듯 설명해줘' → preference 1개
- '겨울이가 나한테 더 많이 물어보고 궁금해줬으면 좋겠어' → preference 1개"""

_VERIFICATION_PROMPT = """너는 장기 기억 후보의 보수적인 검증자다.
recent_context는 의도 구분에만 사용하고 evidence와 candidate를 검증해 JSON 객체 하나만 반환해.

각 조건을 독립적으로 판단하고, accept=true는 아래 조건을 모두 만족할 때만 가능하다.
- evidence가 candidate를 추측 없이 직접 말한다.
- 몇 주 뒤 전혀 다른 주제의 대화에서도 답변을 바꿀 정보다.
- 단순 진행 명령, 질문, 짧은 동의, 순간 감정이 아니다.
- candidate에 '보인다', '원하는 것 같다'처럼 근거에 없는 해석이 없다.
- 사용자가 겨울이의 반복적인 대화·설명 방식을 직접 요청했고 특정 기간으로 제한하지
  않았다면 '앞으로'나 '항상'이라는 단어가 없어도 durable=true다.

조금이라도 애매하면 accept=false다. 후보를 고치거나 새 사실을 만들지 마.

검증 예시:
- evidence='계속해줘', kind='preference' → false
- evidence='좋은 E2E 구조가 뭐야?', kind='project' → false
- evidence='나는 A회사에서 일해', kind='semantic' → true
- evidence='검색 결과를 읽지 말고 이해시키듯 설명해줘', kind='preference' → true
- evidence='앞으로 나한테 더 많이 물어보고 궁금해해 줘', kind='preference' → true"""

_LONG_TERM_KINDS = frozenset(
    {"semantic", "preference", "decision", "project", "procedural"}
)
_LONG_TERM_SCOPES = frozenset({"candidate_stable", "stable"})


class StructuredChatReflectionInterpreter:
    def __init__(self, chat_model: ChatModel) -> None:
        self._chat_model = chat_model

    def extract(self, request: ReflectionExtractionRequest) -> ReflectionExtraction:
        chat_request = ChatRequest(
            prompt=request.user_text,
            messages=(
                ConversationMessage("system", _SYSTEM_PROMPT),
                ConversationMessage(
                    "user",
                    json.dumps(
                        {
                            "recent_context": [
                                {"role": message.role, "content": message.content}
                                for message in request.recent_context
                            ],
                            "current_user_text": request.user_text,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ),
            max_tokens=256,
            response_format=_response_format(request.user_text),
            temperature=0.0,
            seed=42,
        )
        try:
            raw = self._chat_model.generate(chat_request).text
        except AdapterUnavailableError as error:
            raise ReflectionInterpreterUnavailableError(
                f"local reflection interpreter unavailable: {error}"
            ) from error
        candidate = raw.strip()
        if candidate.startswith("```json\n") and candidate.endswith("\n```"):
            candidate = candidate[len("```json\n") : -len("\n```")].strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as error:
            raise ReflectionInterpretationError(
                f"local reflection interpreter returned invalid JSON: {error.msg}"
            ) from error
        extraction = _parse(payload, request.user_text)
        if not extraction.proposals:
            return extraction
        if not self._verify(request, extraction.proposals[0]):
            return ReflectionExtraction(())
        return extraction

    def _verify(
        self,
        request: ReflectionExtractionRequest,
        proposal: MemoryProposal,
    ) -> bool:
        user_text = request.user_text
        verification_request = ChatRequest(
            prompt=user_text,
            messages=(
                ConversationMessage("system", _VERIFICATION_PROMPT),
                ConversationMessage(
                    "user",
                    json.dumps(
                        {
                            "recent_context": [
                                {"role": message.role, "content": message.content}
                                for message in request.recent_context
                            ],
                            "evidence": user_text,
                            "candidate": {
                                "kind": proposal.kind,
                                "content": proposal.content,
                                "temporal_scope": proposal.temporal_scope,
                            },
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ),
            max_tokens=96,
            response_format=_verification_response_format(),
            temperature=0.0,
            seed=43,
        )
        try:
            raw = self._chat_model.generate(verification_request).text
        except AdapterUnavailableError as error:
            raise ReflectionInterpreterUnavailableError(
                f"local reflection verifier unavailable: {error}"
            ) from error
        try:
            payload = json.loads(raw.strip())
        except json.JSONDecodeError as error:
            raise ReflectionInterpretationError(
                f"local reflection verifier returned invalid JSON: {error.msg}"
            ) from error
        fields = {"directly_stated", "durable", "not_momentary", "accept"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ReflectionInterpretationError(
                "reflection verification fields are invalid"
            )
        if any(not isinstance(payload[field], bool) for field in fields):
            raise ReflectionInterpretationError(
                "reflection verification fields must be boolean"
            )
        required = (
            payload["directly_stated"]
            and payload["durable"]
            and payload["not_momentary"]
        )
        return bool(payload["accept"] and required)


def _response_format(user_text: str) -> dict[str, object]:
    memory = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": sorted(_LONG_TERM_KINDS)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "temporal_scope": {
                "type": "string",
                "enum": sorted(_LONG_TERM_SCOPES),
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string", "const": user_text},
                "minItems": 1,
                "maxItems": 1,
            },
        },
        "required": [
            "kind",
            "confidence",
            "temporal_scope",
            "evidence",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "conversation_reflection",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "proposals": {
                        "type": "array",
                        "items": memory,
                        "maxItems": 1,
                    }
                },
                "required": ["proposals"],
                "additionalProperties": False,
            },
        },
    }


def _verification_response_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "conversation_reflection_verification",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "directly_stated": {"type": "boolean"},
                    "durable": {"type": "boolean"},
                    "not_momentary": {"type": "boolean"},
                    "accept": {"type": "boolean"},
                },
                "required": [
                    "directly_stated",
                    "durable",
                    "not_momentary",
                    "accept",
                ],
                "additionalProperties": False,
            },
        },
    }


def _parse(payload: object, user_text: str) -> ReflectionExtraction:
    if not isinstance(payload, dict) or set(payload) != {"proposals"}:
        raise ReflectionInterpretationError(
            "reflection output must contain only proposals"
        )
    raw_proposals = payload["proposals"]
    if not isinstance(raw_proposals, list) or len(raw_proposals) > 1:
        raise ReflectionInterpretationError("reflection proposals must be an array of at most 1")
    proposals: list[MemoryProposal] = []
    expected = {"kind", "confidence", "temporal_scope", "evidence"}
    for raw in raw_proposals:
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ReflectionInterpretationError("reflection proposal fields are invalid")
        kind = raw["kind"]
        confidence = raw["confidence"]
        scope = raw["temporal_scope"]
        evidence = raw["evidence"]
        if kind not in _LONG_TERM_KINDS:
            raise ReflectionInterpretationError(f"unsupported reflection kind: {kind}")
        if scope not in _LONG_TERM_SCOPES:
            raise ReflectionInterpretationError(
                f"unsupported reflection temporal scope: {scope}"
            )
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise ReflectionInterpretationError(
                "reflection confidence must be between 0 and 1"
            )
        if evidence != [user_text]:
            raise ReflectionInterpretationError(
                "reflection evidence must be the exact current user text"
            )
        proposals.append(
            MemoryProposal(
                kind=kind,
                content=user_text.strip(),
                confidence=float(confidence),
                temporal_scope=scope,
                evidence=(user_text,),
            )
        )
    return ReflectionExtraction(tuple(proposals))
