"""Compact local-LLM extraction of review-only conversational memories."""

from __future__ import annotations

import json

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import ChatRequest, ConversationMessage
from companion.ports import ChatModel
from companion.reflection import (
    CurrentStateProposal,
    ReflectionExtraction,
    ReflectionExtractionRequest,
    ReflectionInterpretationError,
    ReflectionInterpreterUnavailableError,
)
from companion.turn_understanding import MemoryProposal

_SYSTEM_PROMPT = """너는 천우와 겨울이의 과거 대화에서 장기 기억과 현재 상태 후보를 찾는다.
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
- current_state_updates를 하나라도 제안한 문장은 장기 proposals에 중복 제안하지 않는다.
- 기억 본문은 시스템이 실제 원문으로 저장한다. 별도 요약문을 만들지 않는다.
- evidence에는 입력의 current_user_text 전체를 글자 하나 바꾸지 않고 넣는다.
- 한 문장에서 미래에 가장 유용한 후보 하나만 제안하고, 없으면 빈 배열을 반환한다.
- 이 결과는 자동 기억이 아니라 사용자가 검토할 후보일 뿐이다.

current_state_updates는 이미 저장된 current_states 중 천우가 지금 상태의 변화나 지속을
직접 말한 경우에만 만든다.
- topic은 current_states에 있는 값을 그대로 사용하고 새 topic을 만들지 않는다.
- 질문, 추측, 제3자의 상태, 단순 과거 회상은 상태 변경이 아니다.
- current_state_updates에는 topic과 confidence만 쓴다. evidence와 기억 본문은 시스템이
  current_user_text 전체로 고정하므로 직접 만들거나 current_states의 content를 복사하지 않는다.
- 원인이나 대상이 드러나면 일반적인 마음 상태보다 그 원인을 직접 다루는 가장 구체적인
  topic을 고른다. 회사·공부 부담은 work 관련 topic이고 일반 wellbeing이 아니다.
- 이전과 달라졌다는 말뿐 아니라 '아직', '여전히', '계속'처럼 현재까지 상태가 이어진다는
  말도 후보가 된다.
- 서로 다른 절이 서로 다른 topic을 말하면 topic마다 항목 하나로 분리한다.
- '그건 괜찮아'처럼 문장 자체로 topic을 알 수 없는 대명사는 recent_context가 바로 그
  topic의 현재 상태를 물었을 때만 제안한다. current_states 목록 자체는 대명사 근거가 아니다.
- current_state_updates_allowed=false면 반드시 빈 배열을 반환한다.
- 한 문장에서 서로 다른 기존 topic은 최대 3개까지 분리할 수 있다.

판단 예시:
- '계속해줘' → 후보 없음
- '좋은 E2E 아키텍처가 뭐야?' → 후보 없음. 질문의 주제를 프로젝트로 만들지 않음
- '오늘 힘들어서 운동했어' → 후보 없음. 순간 경험임
- '나는 A회사에서 일해' → semantic 1개
- '나는 B 기술을 적용하는 프로젝트를 진행해' → project 1개
- '정보를 읽듯 전하지 말고 나를 이해시키듯 설명해줘' → preference 1개
- '겨울이가 나한테 더 많이 물어보고 궁금해줬으면 좋겠어' → preference 1개"""

_STATE_VERIFICATION_PROMPT = """너는 현재 상태 변경 후보의 보수적인 검증자다.
recent_context와 current_states는 대명사, 변화 방향과 가장 구체적인 topic을 비교하는 데만 사용한다.
JSON 객체 하나만 반환해.

accept=true는 아래 조건을 모두 만족할 때만 가능하다.
- 천우 자신의 현재 상태 또는 현재까지 이어지는 상태를 직접 말한다.
- '아직', '여전히', '계속'은 현재까지 이어지는 상태이므로 current_condition=true다.
- 선택한 topic이 current_states 전체 중 원인과 대상을 가장 구체적으로 다룬다.
- current_user_text 자체 또는 recent_context의 직접 질문이 topic을 특정한다.
- 대명사만 있고 recent_context가 topic을 특정하지 않으면 topic_grounded=false다.
- 질문, 추측, 미래 가정, 제3자 상태, 과거 회상만으로 만든 후보가 아니다.

후보는 자동 반영되지 않고 천우가 직접 확인한다. 그래도 애매하면 accept=false다."""

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
_DEICTIC_STATE_REFERENCES = (
    "그건",
    "그거",
    "그것",
    "그 부분",
    "그 상태",
    "그 문제",
)


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
                            "current_state_updates_allowed": (
                                request.allow_current_state_updates
                            ),
                            "current_states": [
                                {
                                    "topic": state.topic,
                                    "memory_id": state.memory_id,
                                    "content": state.content,
                                }
                                for state in request.current_states
                            ],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ),
            max_tokens=256,
            response_format=_response_format(request),
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
        raw_state_output_present = (
            isinstance(payload, dict)
            and isinstance(payload.get("current_state_updates"), list)
            and bool(payload["current_state_updates"])
        )
        extraction = _parse(payload, request)
        # A model may duplicate a momentary state as a durable semantic fact.
        # State classification wins even if its verifier later rejects it; the
        # same utterance must never leak into long-term Memory through this path.
        proposals = () if raw_state_output_present else extraction.proposals
        if proposals and not self._verify(request, proposals[0]):
            proposals = ()
        warnings = list(extraction.warnings)
        if _has_ungrounded_state_reference(request):
            state_updates: tuple[CurrentStateProposal, ...] = ()
        else:
            verified_updates: list[CurrentStateProposal] = []
            for proposal in extraction.current_state_updates:
                try:
                    accepted = self._verify_current_state(request, proposal)
                except ReflectionInterpreterUnavailableError:
                    raise
                except ReflectionInterpretationError as error:
                    warnings.append(
                        f"current-state update {proposal.topic} ignored: "
                        f"verifier contract failed: {error}"
                    )
                else:
                    if accepted:
                        verified_updates.append(proposal)
            state_updates = tuple(verified_updates)
        return ReflectionExtraction(proposals, state_updates, tuple(warnings))

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

    def _verify_current_state(
        self,
        request: ReflectionExtractionRequest,
        proposal: CurrentStateProposal,
    ) -> bool:
        previous = next(
            state for state in request.current_states if state.topic == proposal.topic
        )
        verification_request = ChatRequest(
            prompt=request.user_text,
            messages=(
                ConversationMessage("system", _STATE_VERIFICATION_PROMPT),
                ConversationMessage(
                    "user",
                    json.dumps(
                        {
                            "recent_context": [
                                {"role": message.role, "content": message.content}
                                for message in request.recent_context
                            ],
                            "current_user_text": request.user_text,
                            "previous_state": {
                                "topic": previous.topic,
                                "content": previous.content,
                            },
                            "current_states": [
                                {
                                    "topic": state.topic,
                                    "content": state.content,
                                }
                                for state in request.current_states
                            ],
                            "candidate": {
                                "topic": proposal.topic,
                                "evidence": proposal.content,
                            },
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ),
            max_tokens=96,
            response_format=_state_verification_response_format(),
            temperature=0.0,
            seed=44,
        )
        try:
            raw = self._chat_model.generate(verification_request).text
        except AdapterUnavailableError as error:
            raise ReflectionInterpreterUnavailableError(
                f"local current-state verifier unavailable: {error}"
            ) from error
        try:
            payload = json.loads(raw.strip())
        except json.JSONDecodeError as error:
            raise ReflectionInterpretationError(
                f"local current-state verifier returned invalid JSON: {error.msg}"
            ) from error
        fields = {
            "about_user",
            "matches_most_specific_topic",
            "topic_grounded",
            "current_condition",
            "not_hypothetical_or_question",
            "accept",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ReflectionInterpretationError(
                "current-state verification fields are invalid"
            )
        if any(not isinstance(payload[field], bool) for field in fields):
            raise ReflectionInterpretationError(
                "current-state verification fields must be boolean"
            )
        required = all(payload[field] for field in fields - {"accept"})
        return bool(payload["accept"] and required)


def _response_format(request: ReflectionExtractionRequest) -> dict[str, object]:
    user_text = request.user_text
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
    topics = [state.topic for state in request.current_states]
    topic_schema: dict[str, object] = {"type": "string"}
    if topics:
        topic_schema["enum"] = topics
    state_update = {
        "type": "object",
        "properties": {
            "topic": topic_schema,
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["topic", "confidence"],
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
                    },
                    "current_state_updates": {
                        "type": "array",
                        "items": state_update,
                        "maxItems": (
                            3 if request.allow_current_state_updates and topics else 0
                        ),
                    },
                },
                "required": ["proposals", "current_state_updates"],
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


def _state_verification_response_format() -> dict[str, object]:
    fields = (
        "about_user",
        "matches_most_specific_topic",
        "topic_grounded",
        "current_condition",
        "not_hypothetical_or_question",
        "accept",
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "current_state_update_verification",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {field: {"type": "boolean"} for field in fields},
                "required": list(fields),
                "additionalProperties": False,
            },
        },
    }


def _parse(
    payload: object, request: ReflectionExtractionRequest
) -> ReflectionExtraction:
    user_text = request.user_text
    expected_output = {"proposals", "current_state_updates"}
    if not isinstance(payload, dict) or set(payload) != expected_output:
        raise ReflectionInterpretationError(
            "reflection output fields are invalid"
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
    raw_state_updates = payload["current_state_updates"]
    if not isinstance(raw_state_updates, list) or len(raw_state_updates) > 3:
        raise ReflectionInterpretationError(
            "current-state updates must be an array of at most 3"
        )
    if raw_state_updates and not request.allow_current_state_updates:
        raise ReflectionInterpretationError("current-state updates are disabled")
    states_by_topic = {state.topic: state for state in request.current_states}
    state_updates: list[CurrentStateProposal] = []
    warnings: list[str] = []
    expected_state_fields = {"topic", "confidence"}
    seen_topics: set[str] = set()
    for index, raw in enumerate(raw_state_updates, start=1):
        warning_prefix = f"current-state update {index} ignored: "
        if not isinstance(raw, dict) or set(raw) != expected_state_fields:
            warnings.append(warning_prefix + "fields are invalid")
            continue
        topic = raw["topic"]
        confidence = raw["confidence"]
        if topic not in states_by_topic:
            warnings.append(warning_prefix + "topic is not active")
            continue
        if topic in seen_topics:
            warnings.append(warning_prefix + "topic is duplicated")
            continue
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            warnings.append(warning_prefix + "confidence must be between 0 and 1")
            continue
        seen_topics.add(topic)
        state_updates.append(
            CurrentStateProposal(
                topic=topic,
                content=user_text,
                confidence=float(confidence),
                evidence=(user_text,),
                supersedes_memory_id=states_by_topic[topic].memory_id,
            )
        )
    return ReflectionExtraction(
        tuple(proposals),
        tuple(state_updates),
        tuple(warnings),
    )


def _has_ungrounded_state_reference(
    request: ReflectionExtractionRequest,
) -> bool:
    if request.recent_context:
        return False
    return any(
        reference in request.user_text
        for reference in _DEICTIC_STATE_REFERENCES
    )
