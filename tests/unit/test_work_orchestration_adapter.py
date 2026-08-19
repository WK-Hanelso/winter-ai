import json

from companion.adapters.work_orchestration import StructuredChatWinterExecutive
from companion.contracts import ChatRequest, ChatResult
from companion.work_orchestration import EvidenceReport, WorkContext, WorkRequest


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
