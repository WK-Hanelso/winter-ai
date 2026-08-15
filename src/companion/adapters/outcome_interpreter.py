"""Local structured evaluator for the user's next-turn outcome signals."""

from __future__ import annotations

from dataclasses import dataclass
import json

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import ChatRequest, ConversationMessage
from companion.outcome import (
    AFFECT_SHIFTS,
    INTENT_MATCHES,
    OUTCOMES,
    USEFULNESS,
    OutcomeAssessment,
    OutcomeInterpretationError,
    OutcomeInterpretationRequest,
    OutcomeInterpreterUnavailableError,
    OutcomeMemoryClaim,
)
from companion.ports import ChatModel

_CATEGORICAL_SYSTEM_PROMPT = """너는 겨울이의 실제 답변이 사용자의 이전 의도를
충족했는지 다음 사용자 반응으로 사후 평가하는 분석기다. 사용자에게 답하지 말고
response schema의 JSON 객체 하나만 반환해.

먼저 비교 대상을 고정해:
- prior_understanding.conversational_need = 답변이 충족해야 했던 이전 사용자 의도
- assistant_response = 실제로 평가할 겨울이 답변
- next_user_text = 결과를 판정할 수 있는 유일한 관찰 근거
- intent_match는 assistant_response가 이전 사용자 의도를 충족했는지를 next_user_text로
  판정하는 필드다. next_user_text 자체의 새 의도를 prior intent와 비교하는 필드가 아니다.

evidence 규칙:
- 모든 evidence에는 next_user_text에서 글자와 문장부호를 바꾸지 않은 exact substring만 써.
- prior_user_text나 assistant_response를 evidence에 절대 복사하지 마.
- 적절한 짧은 substring을 확신하지 못하면 next_user_text 전체를 그대로 써.

outcome 우선순위:
1. 사용자가 작업·주제를 그만두거나 중단한다고 직접 말하면 abandoned.
2. 사용자가 원했던 반응 방식·의도·정답을 새로 밝혀 바로잡으면 반드시 corrected.
   'A 말고 B', 'A보다 B를 원했다'처럼 대안을 밝히면 rejected보다 corrected를 우선해.
3. 사용자가 답이 틀림·아님·도움 안 됨을 명시하고 별도 교정을 주지 않으면 rejected.
4. 같은 요청의 다음 단계·추가 설명을 요구하면 continued. 단순 동의와 같지 않다.
5. 답이 맞음·해결됨·원한 방향임을 직접 확인하면 accepted.
6. 짧은 추임새, 침묵, 단순 주제 전환처럼 근거가 부족하면 unclear.

세부 필드:
- intent_match=confirmed: 답이 이전 의도를 충족했다고 직접 확인.
- intent_match=contradicted: 다른 반응을 원했다고 밝히거나 핵심이 틀렸다고 직접 확인.
- intent_match=partial: 일부는 이어가지만 추가 단계가 필요하다는 직접 근거가 있음.
- response_usefulness=helpful: 해결됨, 도움 됨, 맞음, 긍정하며 같은 작업을 이어 달라는 직접 근거.
- response_usefulness=unhelpful: 틀림, 답을 못 얻음, 원한 반응을 놓침, 더 나빠짐의 직접 근거.
- abandoned만으로 답이 unhelpful이었다고 추론하지 마. 별도 직접 근거가 없으면 unclear.
- affect_shift는 사용자가 마음이 놓임·더 답답함처럼 감정 변화를 직접 말할 때만 판정.
- '더 답답해졌다', '더 불안해졌다'처럼 이전보다 부정 감정이 커졌다고 직접 말하면 worsened.
- 여러 해석이 가능하면 unclear와 uncertainties를 사용해.
- Memory나 improvement 신호는 여기서 판정하지 마. schema의 categorical 필드만 반환해.

경계 예시(아래 문장을 복사하지 말고 의미 경계만 적용):
- 해결을 원한 사람에게 위로만 했고 다음 말이 '공감 말고 원인을 알려줘'라면
  corrected / contradicted / unhelpful / affect unclear.
- 다음 말이 '이 작업은 여기서 중단할게'뿐이면 abandoned지만 usefulness와 affect는
  직접 근거가 없으므로 unclear.
- 다음 말이 '그 답 때문에 전보다 더 불안해졌어'라면 rejected / contradicted /
  unhelpful / worsened."""

_SIGNAL_SYSTEM_PROMPT = """너는 겨울이 답변에 실제로 사용된 장기 Memory claim을
다음 사용자 반응이 확인하거나 반박했는지만 판정하는 보수적인 분석기다.

공통 원칙:
- next_user_text만 관찰 근거다.
- payload의 memory_claims는 Core가 실제 답변에 주입한 Memory만 담는다.
- 목록의 각 claim_id를 정확히 한 번씩 반환하고 새 ID나 Memory를 만들지 마.
- 단순 긍정·부정·감정 변화만으로 관계를 만들지 마.
- 정의를 만족하지 않으면 relation은 none, 애매하면 unclear로 둬.

관계 정의:
- assistant_response가 claim 내용을 사용자 Memory로 말했고 next_user_text가 맞다고 직접
  확인할 때만 confirmed.
- assistant_response가 claim 내용을 말했고 next_user_text가 틀렸다고 직접 부정하거나
  실제 값으로 교정할 때만 contradicted.
- assistant_response가 claim을 사용했더라도 next_user_text가 그 Memory 자체를 평가하지
  않으면 none.
- 해결됨·도움 됨·마음이 놓임·작업 중단은 Memory 확인이나 반박이 아니다.

경계 예시(문장을 복사하지 말고 의미 경계만 적용):
- 겨울이가 '사용자는 A를 선호한다'고 말하고 사용자가 '맞아, A가 좋아'라고 하면
  해당 claim은 confirmed.
- 같은 상황에서 사용자가 '아니, B가 좋아'라고 하면 해당 claim은 contradicted.
- 사용자가 '그 방법으로 해결됐어' 또는 '여기서 중단할게'라고 하면 claim relation은 none.

schema 밖 필드는 만들지 마."""

_MEMORY_RELATIONS = frozenset({"confirmed", "contradicted", "none", "unclear"})


@dataclass(frozen=True)
class _MemoryRelation:
    claim_id: str
    relation: str
    confidence: float


@dataclass(frozen=True)
class _MemoryDecisions:
    relations: tuple[_MemoryRelation, ...]
    uncertainties: tuple[str, ...]


class StructuredChatOutcomeInterpreter:
    def __init__(self, chat_model: ChatModel) -> None:
        self._chat_model = chat_model

    def evaluate(self, request: OutcomeInterpretationRequest) -> OutcomeAssessment:
        payload: dict[str, object] = {
            "prior_user_text": request.prior_user_text,
            "prior_understanding": request.prior_understanding.to_dict(),
            "assistant_response": request.assistant_response,
            "next_user_text": request.next_user_text,
        }
        categorical_raw = self._generate_json(
            request,
            _CATEGORICAL_SYSTEM_PROMPT,
            payload,
            _categorical_response_format(request.next_user_text),
            stage="categorical",
        )
        categorical = _parse_categorical(
            categorical_raw, request.next_user_text
        )
        memory_decisions = _MemoryDecisions((), ())
        if request.memory_claims:
            memory_payload: dict[str, object] = {
                **payload,
                "memory_claims": [
                    {
                        "id": claim.id,
                        "kind": claim.kind,
                        "content": claim.content,
                    }
                    for claim in request.memory_claims
                ],
            }
            signals_raw = self._generate_json(
                request,
                _SIGNAL_SYSTEM_PROMPT,
                memory_payload,
                _memory_response_format(request.memory_claims),
                stage="memory",
            )
            memory_decisions = _parse_memory_decisions(
                signals_raw, request.memory_claims
            )
        claim_by_id = {claim.id: claim for claim in request.memory_claims}
        memory_confirmation = _memory_signals(
            memory_decisions, claim_by_id, "confirmed", request.next_user_text
        )
        memory_contradiction = _memory_signals(
            memory_decisions,
            claim_by_id,
            "contradicted",
            request.next_user_text,
        )
        improvement_confirmation = _derived_improvement_signal(
            categorical, request.next_user_text
        )
        return OutcomeAssessment.from_dict(
            {
                "schema_version": 1,
                "outcome": categorical.outcome,
                "confidence": categorical.confidence,
                "evidence": list(categorical.evidence),
                "intent_match": categorical.intent_match,
                "response_usefulness": categorical.response_usefulness,
                "affect_shift": categorical.affect_shift,
                "memory_confirmation": memory_confirmation,
                "memory_contradiction": memory_contradiction,
                "improvement_confirmation": improvement_confirmation,
                "uncertainties": [
                    *categorical.uncertainties,
                    *memory_decisions.uncertainties,
                ],
            },
            evidence_text=request.next_user_text,
        )

    def _generate_json(
        self,
        request: OutcomeInterpretationRequest,
        system_prompt: str,
        payload: dict[str, object],
        response_format: dict[str, object],
        *,
        stage: str,
    ) -> object:
        chat_request = ChatRequest(
            prompt=request.next_user_text,
            messages=(
                ConversationMessage("system", system_prompt),
                ConversationMessage(
                    "user",
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            ),
            max_tokens=512,
            response_format=response_format,
            temperature=0.0,
            seed=42,
        )
        try:
            raw = self._chat_model.generate(chat_request).text
        except AdapterUnavailableError as error:
            raise OutcomeInterpreterUnavailableError(
                f"local outcome {stage} interpreter unavailable: {error}"
            ) from error
        candidate = raw.strip()
        if candidate.startswith("```json\n") and candidate.endswith("\n```"):
            candidate = candidate[len("```json\n") : -len("\n```")].strip()
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError as error:
            preview = " ".join(raw.strip().split())[:240] or "<empty>"
            raise OutcomeInterpretationError(
                f"local outcome {stage} interpreter did not return valid JSON: "
                f"{error.msg}; output={preview!r}"
            ) from error
        return decoded


def _schema_parts(
    evidence_text: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    string = {"type": "string", "minLength": 1}
    evidence = {
        "type": "array",
        "items": {"type": "string", "const": evidence_text},
        "minItems": 1,
    }
    confidence = {"type": "number", "minimum": 0, "maximum": 1}
    signal = _object_schema(
        {"content": string, "confidence": confidence, "evidence": evidence},
        ("content", "confidence", "evidence"),
    )
    return string, evidence, {"type": "array", "items": signal}


def _categorical_response_format(evidence_text: str) -> dict[str, object]:
    string, evidence, _ = _schema_parts(evidence_text)
    string_array = {"type": "array", "items": string}
    confidence = {"type": "number", "minimum": 0, "maximum": 1}
    root = _object_schema(
        {
            "schema_version": {"type": "integer", "const": 1},
            "outcome": {"type": "string", "enum": sorted(OUTCOMES)},
            "confidence": confidence,
            "evidence": evidence,
            "intent_match": {
                "type": "string",
                "enum": sorted(INTENT_MATCHES),
            },
            "response_usefulness": {
                "type": "string",
                "enum": sorted(USEFULNESS),
            },
            "affect_shift": {
                "type": "string",
                "enum": sorted(AFFECT_SHIFTS),
            },
            "uncertainties": string_array,
        },
        (
            "schema_version",
            "outcome",
            "confidence",
            "evidence",
            "intent_match",
            "response_usefulness",
            "affect_shift",
            "uncertainties",
        ),
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "categorical_outcome_assessment",
            "strict": True,
            "schema": root,
        },
    }


def _memory_response_format(
    claims: tuple[OutcomeMemoryClaim, ...],
) -> dict[str, object]:
    string = {"type": "string", "minLength": 1}
    confidence = {"type": "number", "minimum": 0, "maximum": 1}
    relation = _object_schema(
        {
            "claim_id": {
                "type": "string",
                "enum": sorted(claim.id for claim in claims),
            },
            "relation": {
                "type": "string",
                "enum": sorted(_MEMORY_RELATIONS),
            },
            "confidence": confidence,
        },
        ("claim_id", "relation", "confidence"),
    )
    root = _object_schema(
        {
            "schema_version": {"type": "integer", "const": 1},
            "memory_relations": {
                "type": "array",
                "items": relation,
                "minItems": len(claims),
                "maxItems": len(claims),
            },
            "uncertainties": {"type": "array", "items": string},
        },
        (
            "schema_version",
            "memory_relations",
            "uncertainties",
        ),
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "outcome_memory_relations",
            "strict": True,
            "schema": root,
        },
    }


def _parse_categorical(payload: object, evidence_text: str) -> OutcomeAssessment:
    if not isinstance(payload, dict):
        raise OutcomeInterpretationError("categorical outcome must be an object")
    return OutcomeAssessment.from_dict(
        {
            **payload,
            "memory_confirmation": [],
            "memory_contradiction": [],
            "improvement_confirmation": [],
        },
        evidence_text=evidence_text,
    )


def _parse_memory_decisions(
    payload: object,
    claims: tuple[OutcomeMemoryClaim, ...],
) -> _MemoryDecisions:
    if not isinstance(payload, dict):
        raise OutcomeInterpretationError("memory relations must be an object")
    expected = {"schema_version", "memory_relations", "uncertainties"}
    if set(payload) != expected:
        raise OutcomeInterpretationError("memory relation fields mismatch")
    if payload["schema_version"] != 1:
        raise OutcomeInterpretationError("unsupported memory relation schema version")
    raw_relations = payload["memory_relations"]
    if not isinstance(raw_relations, list):
        raise OutcomeInterpretationError("memory_relations must be a list")
    expected_ids = {claim.id for claim in claims}
    relations: list[_MemoryRelation] = []
    for item in raw_relations:
        if not isinstance(item, dict) or set(item) != {
            "claim_id",
            "relation",
            "confidence",
        }:
            raise OutcomeInterpretationError("memory relation entry fields mismatch")
        claim_id = item["claim_id"]
        if not isinstance(claim_id, str) or claim_id not in expected_ids:
            raise OutcomeInterpretationError(f"unknown memory claim id: {claim_id!r}")
        relations.append(
            _MemoryRelation(
                claim_id,
                _enum_value(item["relation"], _MEMORY_RELATIONS, "relation"),
                _confidence_value(item["confidence"], "memory confidence"),
            )
        )
    observed_ids = [relation.claim_id for relation in relations]
    if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != expected_ids:
        raise OutcomeInterpretationError(
            "memory relations must contain every claim id exactly once"
        )
    uncertainties = payload["uncertainties"]
    if not isinstance(uncertainties, list) or not all(
        isinstance(item, str) and item.strip() for item in uncertainties
    ):
        raise OutcomeInterpretationError("uncertainties must contain strings")
    return _MemoryDecisions(tuple(relations), tuple(uncertainties))


def _memory_signals(
    decisions: _MemoryDecisions,
    claims: dict[str, OutcomeMemoryClaim],
    expected: str,
    evidence_text: str,
) -> list[dict[str, object]]:
    return [
        {
            "content": claims[decision.claim_id].content,
            "confidence": decision.confidence,
            "evidence": [evidence_text],
        }
        for decision in decisions.relations
        if decision.relation == expected
    ]


def _derived_improvement_signal(
    assessment: OutcomeAssessment, evidence_text: str
) -> list[dict[str, object]]:
    negative_outcome = assessment.outcome in {"corrected", "rejected"}
    direct_failure = (
        assessment.intent_match == "contradicted"
        or assessment.response_usefulness == "unhelpful"
        or assessment.affect_shift == "worsened"
    )
    if not negative_outcome or not direct_failure:
        return []
    return [
        {
            "content": evidence_text,
            "confidence": assessment.confidence,
            "evidence": [evidence_text],
        }
    ]


def _enum_value(value: object, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise OutcomeInterpretationError(f"invalid {label}: {value!r}")
    return value


def _confidence_value(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OutcomeInterpretationError(f"{label} must be numeric")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise OutcomeInterpretationError(f"{label} must be between 0 and 1")
    return confidence


def _object_schema(
    properties: dict[str, object], required: tuple[str, ...]
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
