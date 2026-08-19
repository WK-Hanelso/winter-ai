"""Structured local-model adapter for Winter-owned work planning and review."""

from __future__ import annotations

import json

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import ChatRequest, ConversationMessage
from companion.ports import ChatModel
from companion.work_orchestration import (
    CAPABILITY_KINDS,
    RESOURCE_KINDS,
    EvidenceReport,
    IntentBrief,
    IntentProposal,
    ReviewProposal,
    WinterDecision,
    WorkOrchestrationError,
    WorkOrchestrationUnavailableError,
    WorkRequest,
    authorize_intent,
)

_INTENT_PROMPT = """너는 겨울이의 단 하나의 Core Model이다.
천우의 현재 요청을 짧은 의미 proposal JSON으로 정리해. 실행·공유·권한 승인은 하지 마.

규칙:
- objective와 desired_outcome은 각각 한 문장만 쓴다.
- capability와 resource는 현재 요청을 완수하는 데 필요한 **가장 강한 권한
  하나씩만** enum에서 고르고, 요청의 정확한 부분문자열을 evidence로 붙인다.
  Worker 이름·저장소 구조·파일 위치는 Worker가 읽어 확인할 수 있으므로 불확실성이 아니다.
- project.inspect는 코드·문서·상태를 읽는 일, project.modify는 파일 변경,
  deployment.execute는 배포, account.read는 메일·계정 데이터 읽기다.
- 천우의 선택에 따라 작업 목표가 바뀌어 Worker가 시작할 수 없을 때만 불확실성
  하나와 구체적인 질문 하나를 쓴다. 아니면 둘 다 비운다.
- definition_of_done은 관찰 가능한 핵심 조건만 최대 3개 쓴다.
- JSON 밖의 설명은 쓰지 마."""

_REVIEW_PROMPT = """너는 겨울이의 단 하나의 Core Model이다.
IntentBrief와 Worker EvidenceReport를 비교해 짧은 review proposal JSON을 작성해.
최종 action은 정책 코드가 결정하므로 answer/retry/clarify 필드는 만들지 마.

규칙:
- supported는 완료 기준이 근거와 pass check로 모두 충족된 경우만 쓴다.
- 일부 누락은 partial, 실패·근거 없음은 unsupported, 천우 선택이 필요하면 needs_user다.
- evidence_ids에는 report에 있는 ID만 그대로 복사하고 설명을 붙이지 마.
- message는 supported면 겨울이의 결론, 그 외에는 다음 Worker 작업이나 천우 질문 한 문장이다.
- goal_coverage와 unresolved는 각각 최대 3개, judgment는 한 문장이다.
- Worker 보고를 그대로 전달하거나 말투만 바꾸지 마. JSON 밖의 설명은 쓰지 마."""


class StructuredChatWinterExecutive:
    """Uses one selected Core Model for semantic proposals, never authorization."""

    def __init__(self, chat_model: ChatModel) -> None:
        self._chat_model = chat_model

    def create_brief(self, request: WorkRequest) -> IntentBrief:
        payload: dict[str, object] = {
            "current_user_text": request.user_text,
            "worker_context": list(request.worker_context),
        }
        decoded = self._generate(
            _INTENT_PROMPT,
            payload,
            _intent_response_format(),
            max_tokens=500,
        )
        proposal = IntentProposal.from_dict(decoded, request=request)
        return authorize_intent(proposal, request)

    def review(
        self, brief: IntentBrief, report: EvidenceReport
    ) -> WinterDecision:
        payload: dict[str, object] = {
            "intent_brief": brief.to_dict(),
            "worker_report": report.to_dict(),
        }
        decoded = self._generate(
            _REVIEW_PROMPT,
            payload,
            _review_response_format(report.evidence_ids),
            max_tokens=400,
        )
        proposal = ReviewProposal.from_dict(decoded, report=report)
        return WinterDecision.from_review(proposal, brief=brief, report=report)

    def _generate(
        self,
        system_prompt: str,
        payload: dict[str, object],
        response_format: dict[str, object],
        *,
        max_tokens: int,
    ) -> object:
        request = ChatRequest(
            prompt="structured Winter work proposal",
            messages=(
                ConversationMessage("system", system_prompt),
                ConversationMessage(
                    "user",
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            ),
            max_tokens=max_tokens,
            response_format=response_format,
            temperature=0.0,
            seed=42,
        )
        try:
            raw = self._chat_model.generate(request).text
        except AdapterUnavailableError as error:
            raise WorkOrchestrationUnavailableError(
                f"local Winter Executive unavailable: {error}"
            ) from error
        candidate = raw.strip()
        if candidate.startswith("```json\n") and candidate.endswith("\n```"):
            candidate = candidate[len("```json\n") : -len("\n```")].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as error:
            preview = " ".join(raw.strip().split())[:240] or "<empty>"
            raise WorkOrchestrationError(
                "local Winter Executive did not return valid JSON: "
                f"{error.msg}; output={preview!r}"
            ) from error


def _string() -> dict[str, object]:
    return {"type": "string", "minLength": 1}


def _nullable_string() -> dict[str, object]:
    return {"anyOf": [_string(), {"type": "null"}]}


def _strings(*, minimum: int = 0, maximum: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "type": "array",
        "items": _string(),
        "minItems": minimum,
    }
    if maximum is not None:
        result["maxItems"] = maximum
    return result


def _object(
    properties: dict[str, object], required: tuple[str, ...]
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _format(name: str, root: dict[str, object]) -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": root},
    }


def _grounded_enum(values: frozenset[str]) -> dict[str, object]:
    return _object(
        {
            "kind": {"type": "string", "enum": sorted(values)},
            "evidence": _strings(minimum=1, maximum=2),
        },
        ("kind", "evidence"),
    )


def _intent_response_format() -> dict[str, object]:
    fields = (
        "schema_version",
        "objective",
        "desired_outcome",
        "capabilities",
        "resources",
        "definition_of_done",
        "uncertainties",
        "clarification_question",
    )
    return _format(
        "winter_intent_proposal_v2",
        _object(
            {
                "schema_version": {"type": "integer", "const": 2},
                "objective": _string(),
                "desired_outcome": _string(),
                "capabilities": {
                    "type": "array",
                    "items": _grounded_enum(CAPABILITY_KINDS),
                    "minItems": 1,
                    "maxItems": 1,
                },
                "resources": {
                    "type": "array",
                    "items": _grounded_enum(RESOURCE_KINDS),
                    "minItems": 1,
                    "maxItems": 1,
                },
                "definition_of_done": _strings(minimum=1, maximum=3),
                "uncertainties": _strings(maximum=1),
                "clarification_question": _nullable_string(),
            },
            fields,
        ),
    )


def _evidence_id_array(known: frozenset[str]) -> dict[str, object]:
    if not known:
        return {
            "type": "array",
            "items": _string(),
            "minItems": 0,
            "maxItems": 0,
        }
    return {
        "type": "array",
        "items": {"type": "string", "enum": sorted(known)},
        "minItems": 0,
        "maxItems": len(known),
        "uniqueItems": True,
    }


def _review_response_format(known_evidence: frozenset[str]) -> dict[str, object]:
    fields = (
        "schema_version",
        "worker_assessment",
        "goal_coverage",
        "judgment",
        "evidence_ids",
        "unresolved",
        "message",
    )
    return _format(
        "winter_review_proposal_v2",
        _object(
            {
                "schema_version": {"type": "integer", "const": 2},
                "worker_assessment": {
                    "type": "string",
                    "enum": ["supported", "partial", "unsupported", "needs_user"],
                },
                "goal_coverage": _strings(minimum=1, maximum=3),
                "judgment": _string(),
                "evidence_ids": _evidence_id_array(known_evidence),
                "unresolved": _strings(maximum=3),
                "message": _string(),
            },
            fields,
        ),
    )
