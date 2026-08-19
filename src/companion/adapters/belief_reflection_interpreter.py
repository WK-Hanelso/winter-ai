"""Structured local-LLM adapters for Shadow Winter Belief reflection."""

from __future__ import annotations

from dataclasses import asdict
import json

from companion.adapters.fake import AdapterUnavailableError
from companion.belief_reflection import (
    BeliefProposal,
    BeliefReflectionError,
    BeliefReflectionRequest,
    BeliefReflectionUnavailableError,
    BeliefSafetyReview,
)
from companion.contracts import ChatRequest, ConversationMessage
from companion.ports import ChatModel

_PROPOSAL_PROMPT = """너는 겨울이가 실제 대화 결과에서 수정 가능한 자기 관점을 찾는 분석기다.
대화에 답하지 말고 JSON만 반환해.

- proposal은 겨울이 자신의 대화 선택·관계 행동·프로젝트 판단이어야 한다.
- 천우에 대한 사실·감정·의도·성격을 겨울이의 관점으로 우회 저장하지 마.
- Identity, 실제 capability, 권한, Worker 실행 가능 여부를 바꾸거나 주장하지 마.
- 천우의 의존·독점·죄책감·복종을 요구하는 관계 규칙을 만들지 마.
- 한 번의 반응이나 단순 행동 반복이 아니라 후속 결과가 실제로 지지한 경우만 제안해.
- evidence_event_ids와 counterevidence_event_ids에는 입력 event_id만 그대로 사용해.
- 확실한 후보가 없으면 proposal=null을 반환해.
"""

_REVIEW_PROMPT = """너는 겨울이의 자기 관점 후보를 보수적으로 검증하는 독립 검토자다.
JSON만 반환하고 모든 항목을 독립적으로 판단해. 하나라도 불확실하면 accept=false다.

- winter_owned: 천우 사실이 아니라 겨울이 자신의 수정 가능한 판단인가
- low_risk: 대화 방식·관계 행동·프로젝트 판단의 낮은 위험 범위인가
- evidence_entails_stance: 실제 문장과 후속 반응이 이 입장을 직접 지지하는가
- no_user_fact_claim: 천우의 감정·의도·성격·상태를 새 사실로 만들지 않는가
- no_identity_change: Core Identity나 관계 경계를 바꾸지 않는가
- no_capability_claim: 검증되지 않은 능력·행동·Worker를 주장하지 않는가
- no_dependency_or_coercion: 의존·독점·죄책감·복종을 요구하지 않는가
- counterevidence_considered: 반대 근거가 없음을 가정하지 않고 입력의 반증을 확인했는가
"""


class StructuredChatBeliefProposalInterpreter:
    def __init__(self, chat_model: ChatModel) -> None:
        self._chat_model = chat_model

    def propose(self, request: BeliefReflectionRequest) -> BeliefProposal | None:
        result = _generate(
            self._chat_model,
            ChatRequest(
                prompt="Winter Belief proposal",
                messages=(
                    ConversationMessage("system", _PROPOSAL_PROMPT),
                    ConversationMessage("user", _request_json(request)),
                ),
                max_tokens=320,
                response_format=_proposal_response_format(),
                temperature=0.0,
                seed=51,
            ),
        )
        payload = _json_object(result)
        if set(payload) != {"proposal"}:
            raise BeliefReflectionError("belief proposal response fields are invalid")
        raw = payload["proposal"]
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise BeliefReflectionError("belief proposal must be an object or null")
        expected = {
            "subject",
            "stance",
            "rationale",
            "confidence",
            "evidence_event_ids",
            "counterevidence_event_ids",
            "supersedes",
        }
        if set(raw) != expected:
            raise BeliefReflectionError("belief proposal fields are invalid")
        try:
            return BeliefProposal(
                subject=_string(raw["subject"], "subject"),
                stance=_string(raw["stance"], "stance"),
                rationale=_string(raw["rationale"], "rationale"),
                confidence=_confidence(raw["confidence"]),
                evidence_event_ids=_strings(
                    raw["evidence_event_ids"], "evidence_event_ids"
                ),
                counterevidence_event_ids=_strings(
                    raw["counterevidence_event_ids"],
                    "counterevidence_event_ids",
                ),
                supersedes=_optional_string(raw["supersedes"], "supersedes"),
            )
        except BeliefReflectionError:
            raise
        except (TypeError, ValueError) as error:
            raise BeliefReflectionError("belief proposal values are invalid") from error


class StructuredChatBeliefSafetyReviewer:
    def __init__(self, chat_model: ChatModel) -> None:
        self._chat_model = chat_model

    def review(
        self,
        request: BeliefReflectionRequest,
        proposal: BeliefProposal,
    ) -> BeliefSafetyReview:
        result = _generate(
            self._chat_model,
            ChatRequest(
                prompt="Winter Belief safety review",
                messages=(
                    ConversationMessage("system", _REVIEW_PROMPT),
                    ConversationMessage(
                        "user",
                        json.dumps(
                            {
                                "evidence_window": json.loads(_request_json(request)),
                                "proposal": asdict(proposal),
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                ),
                max_tokens=160,
                response_format=_review_response_format(),
                temperature=0.0,
                seed=52,
            ),
        )
        payload = _json_object(result)
        fields = {
            "winter_owned",
            "low_risk",
            "evidence_entails_stance",
            "no_user_fact_claim",
            "no_identity_change",
            "no_capability_claim",
            "no_dependency_or_coercion",
            "counterevidence_considered",
            "accept",
        }
        if set(payload) != fields or any(
            not isinstance(payload[field], bool) for field in fields
        ):
            raise BeliefReflectionError("belief safety review fields are invalid")
        return BeliefSafetyReview(
            winter_owned=bool(payload["winter_owned"]),
            low_risk=bool(payload["low_risk"]),
            evidence_entails_stance=bool(payload["evidence_entails_stance"]),
            no_user_fact_claim=bool(payload["no_user_fact_claim"]),
            no_identity_change=bool(payload["no_identity_change"]),
            no_capability_claim=bool(payload["no_capability_claim"]),
            no_dependency_or_coercion=bool(
                payload["no_dependency_or_coercion"]
            ),
            counterevidence_considered=bool(
                payload["counterevidence_considered"]
            ),
            accept=bool(payload["accept"]),
        )


def _generate(model: ChatModel, request: ChatRequest) -> str:
    try:
        return model.generate(request).text
    except AdapterUnavailableError as error:
        raise BeliefReflectionUnavailableError(
            f"local belief reflection unavailable: {error}"
        ) from error


def _json_object(raw: str) -> dict[str, object]:
    candidate = raw.strip()
    if candidate.startswith("```json\n") and candidate.endswith("\n```"):
        candidate = candidate[len("```json\n") : -len("\n```")].strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise BeliefReflectionError(
            f"belief reflection returned invalid JSON: {error.msg}"
        ) from error
    if not isinstance(payload, dict):
        raise BeliefReflectionError("belief reflection must return a JSON object")
    return payload


def _request_json(request: BeliefReflectionRequest) -> str:
    return json.dumps(
        {
            "window_id": request.window_id,
            "evidence": [asdict(item) for item in request.evidence],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BeliefReflectionError(f"belief proposal {name} must be a string")
    return value.strip()


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _string(value, name)


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise BeliefReflectionError(f"belief proposal {name} must be a string list")
    return tuple(item.strip() for item in value)


def _confidence(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise BeliefReflectionError("belief proposal confidence must be numeric")
    return float(value)


def _proposal_response_format() -> dict[str, object]:
    proposal = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "subject",
            "stance",
            "rationale",
            "confidence",
            "evidence_event_ids",
            "counterevidence_event_ids",
            "supersedes",
        ],
        "properties": {
            "subject": {"type": "string"},
            "stance": {"type": "string"},
            "rationale": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_event_ids": {"type": "array", "items": {"type": "string"}},
            "counterevidence_event_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "supersedes": {"type": ["string", "null"]},
        },
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "winter_belief_proposal",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["proposal"],
                "properties": {"proposal": {"anyOf": [proposal, {"type": "null"}]}},
            },
        },
    }


def _review_response_format() -> dict[str, object]:
    fields = [
        "winter_owned",
        "low_risk",
        "evidence_entails_stance",
        "no_user_fact_claim",
        "no_identity_change",
        "no_capability_claim",
        "no_dependency_or_coercion",
        "counterevidence_considered",
        "accept",
    ]
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "winter_belief_safety_review",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": fields,
                "properties": {field: {"type": "boolean"} for field in fields},
            },
        },
    }
