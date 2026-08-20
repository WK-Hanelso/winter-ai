import json

import pytest

from companion.adapters.work_orchestration import (
    BoundedProjectInspectionExecutive,
    StructuredChatWinterExecutive,
)
from companion.contracts import ChatRequest, ChatResult
from companion.work_orchestration import (
    EvidenceReport,
    WorkContext,
    WorkOrchestrationError,
    WorkRequest,
)


class ScriptedChatModel:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = iter(outputs)
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        return ChatResult(next(self._outputs))


def _intent_payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "objective": "Winter 코드의 현재 병목을 찾는다.",
        "desired_outcome": "가장 먼저 고칠 한 가지를 정한다.",
        "capabilities": [
            {"kind": "project.inspect", "evidence": ["프로젝트 병목을 분석해줘"]}
        ],
        "resources": [
            {"kind": "project", "evidence": ["프로젝트 병목을 분석해줘"]}
        ],
        "definition_of_done": ["가장 큰 병목 하나와 근거가 있다."],
        "uncertainties": [],
        "clarification_question": None,
    }


def _review_payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "worker_assessment": "supported",
        "goal_coverage": ["병목과 근거를 확인했다."],
        "judgment": "읽기 전용 분석만 있고 구현 cycle은 없다.",
        "evidence_ids": ["E1"],
        "unresolved": [],
        "message": "내가 확인해보니 다음은 구현 승인 경로를 만드는 게 맞아.",
    }


def test_one_core_model_proposes_semantics_while_policy_owns_actions() -> None:
    model = ScriptedChatModel(
        [
            json.dumps(_intent_payload(), ensure_ascii=False),
            json.dumps(_review_payload(), ensure_ascii=False),
        ]
    )
    executive = StructuredChatWinterExecutive(model)  # type: ignore[arg-type]
    request = WorkRequest(
        "프로젝트 병목을 분석해줘",
        (
            WorkContext("공개 코드 bundle을 읽는다.", "worker_shareable"),
            WorkContext("천우는 결론부터 듣는다.", "winter_only"),
        ),
    )
    report = EvidenceReport.from_dict(
        {
            "schema_version": 2,
            "summary": "구현 cycle이 없다.",
            "evidence": [
                {"evidence_id": "E1", "locator": "src/companion/worker.py:1"}
            ],
            "claims": [
                {
                    "content": "Worker는 읽기 전용이다.",
                    "evidence_ids": ["E1"],
                }
            ],
            "actions_performed": ["Worker 코드를 읽었다."],
            "checks": [
                {"name": "scope", "status": "pass", "evidence_ids": ["E1"]}
            ],
            "limitations": [],
            "unresolved": [],
        }
    )

    brief = executive.create_brief(request)
    decision = executive.review(brief, report)

    assert brief.authorization.status == "ready"
    assert decision.next_action == "answer"
    assert decision.final_response is not None
    assert len(model.requests) == 2
    assert model.requests[0].max_tokens == 500
    assert model.requests[1].max_tokens == 400
    intent_input = json.loads(model.requests[0].messages[1].content)
    assert intent_input["worker_context"] == ["공개 코드 bundle을 읽는다."]
    assert "결론부터" not in model.requests[0].messages[1].content
    assert "실행·공유·권한 승인은 하지 마" in model.requests[0].messages[0].content
    review_input = json.loads(model.requests[1].messages[1].content)
    assert review_input["worker_report"]["evidence"][0]["evidence_id"] == "E1"
    response_schema = model.requests[1].response_format
    assert response_schema is not None
    assert "E1" in str(response_schema)


def test_bounded_inspection_model_owns_meaning_but_not_permission() -> None:
    model = ScriptedChatModel(
        [
            json.dumps(
                {
                    "objective": "실제 응답 지연의 가장 큰 구간을 찾는다.",
                    "desired_outcome": "근거가 있는 개선 한 가지를 정한다.",
                    "definition_of_done": [
                        "latency 근거를 비교한다.",
                        "개선 한 가지를 제안한다.",
                    ],
                },
                ensure_ascii=False,
            )
        ]
    )
    executive = BoundedProjectInspectionExecutive(model)  # type: ignore[arg-type]

    brief = executive.create_brief(
        WorkRequest("현재 코드와 latency 근거로 병목을 진단해줘")
    )

    assert brief.objective == "실제 응답 지연의 가장 큰 구간을 찾는다."
    assert brief.capabilities == ("project.inspect",)
    assert brief.resources == ("project",)
    assert brief.worker_context == ()
    assert brief.authorization.status == "ready"
    assert model.requests[0].max_tokens == 250
    assert "권한, resource" in model.requests[0].messages[0].content
    schema = model.requests[0].response_format
    assert schema is not None
    assert "capabilities" not in str(schema)
    assert "resources" not in str(schema)


def test_bounded_inspection_review_returns_winter_answer_with_caveats() -> None:
    model = ScriptedChatModel(
        [
            json.dumps(
                {
                    "assessment": "supported",
                    "judgment": "STT 이후 구간이 더 크다.",
                    "evidence_ids": ["E1"],
                    "unresolved": ["Core와 TTS 비중은 아직 분리되지 않았다."],
                    "final_response": (
                        "지금은 STT 이후 구간이 더 커 보여. "
                        "다음에는 Core와 TTS 시간을 나눠 기록하는 게 맞아."
                    ),
                },
                ensure_ascii=False,
            )
        ]
    )
    executive = BoundedProjectInspectionExecutive(model)  # type: ignore[arg-type]
    brief = _bounded_brief()
    report = EvidenceReport.from_dict(
        {
            "schema_version": 2,
            "summary": "STT 이후 구간이 더 크다.",
            "evidence": [{"evidence_id": "E1", "locator": "docs/realtime.md:1"}],
            "claims": [
                {"content": "STT 이후가 더 크다.", "evidence_ids": ["E1"]}
            ],
            "actions_performed": ["latency 기록을 읽었다."],
            "checks": [
                {"name": "근거 비교", "status": "pass", "evidence_ids": ["E1"]}
            ],
            "limitations": ["표본이 네 턴뿐이다."],
            "unresolved": [],
        }
    )

    decision = executive.review(brief, report)

    assert decision.worker_assessment == "partial"
    assert decision.next_action == "answer"
    assert decision.final_response is not None
    assert "Core와 TTS 시간을 나눠" in decision.final_response
    assert model.requests[0].max_tokens == 300


def _bounded_brief():  # type: ignore[no-untyped-def]
    model = ScriptedChatModel(
        [
            json.dumps(
                {
                    "objective": "지연 병목을 찾는다.",
                    "desired_outcome": "개선 한 가지를 정한다.",
                    "definition_of_done": ["병목과 개선을 보고한다."],
                },
                ensure_ascii=False,
            )
        ]
    )
    return BoundedProjectInspectionExecutive(model).create_brief(  # type: ignore[arg-type]
        WorkRequest("현재 코드 latency 원인을 분석해줘")
    )


def test_bounded_inspection_rejects_conversation_context() -> None:
    executive = BoundedProjectInspectionExecutive(ScriptedChatModel([]))  # type: ignore[arg-type]

    with pytest.raises(WorkOrchestrationError, match="conversation context"):
        executive.create_brief(
            WorkRequest(
                "코드 원인을 분석해줘",
                (WorkContext("천우의 과거 대화", "winter_only"),),
            )
        )
