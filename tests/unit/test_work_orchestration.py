import json

import pytest

from companion.work_orchestration import (
    AnswerAction,
    EvidenceReport,
    IntentBrief,
    IntentProposal,
    RetryAction,
    ReviewProposal,
    WinterDecision,
    WorkContext,
    WorkOrchestrationError,
    WorkRequest,
    authorize_intent,
    build_worker_task,
)


def _proposal_payload(
    *,
    capability: str = "project.inspect",
    resource: str = "project",
    evidence: str = "Winter 저장소의 병목을 분석해줘",
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "objective": "Winter 저장소의 가장 큰 병목을 찾는다.",
        "desired_outcome": "가장 먼저 고칠 한 가지를 정한다.",
        "capabilities": [{"kind": capability, "evidence": [evidence]}],
        "resources": [{"kind": resource, "evidence": [evidence]}],
        "definition_of_done": ["병목 하나와 근거가 있다."],
        "uncertainties": [],
        "clarification_question": None,
    }


def _authorized_brief() -> IntentBrief:
    request = WorkRequest("Winter 저장소의 병목을 분석해줘")
    proposal = IntentProposal.from_dict(_proposal_payload(), request=request)
    return authorize_intent(proposal, request)


def _report(*, check_status: str = "pass", unresolved: bool = False) -> EvidenceReport:
    return EvidenceReport.from_dict(
        {
            "schema_version": 2,
            "summary": "TTS가 가장 긴 지연 구간이다.",
            "evidence": [
                {"evidence_id": "E1", "locator": "docs/realtime.md:18"}
            ],
            "claims": [
                {
                    "content": "TTS가 가장 긴 구간이다.",
                    "evidence_ids": ["E1"],
                }
            ],
            "actions_performed": ["지연 문서를 읽었다."],
            "checks": [
                {
                    "name": "회귀 테스트",
                    "status": check_status,
                    "evidence_ids": ["E1"],
                }
            ],
            "limitations": [],
            "unresolved": ["측정이 없다."] if unresolved else [],
        }
    )


def test_intent_proposal_requires_exact_shareable_evidence_and_enums() -> None:
    request = WorkRequest(
        "Winter 저장소의 병목을 분석해줘",
        (
            WorkContext("공개 가능한 latency 문서가 있다.", "worker_shareable"),
            WorkContext("나는 결론부터 듣는 설명을 좋아해", "winter_only"),
        ),
    )

    proposal = IntentProposal.from_dict(_proposal_payload(), request=request)

    assert proposal.capabilities[0].kind == "project.inspect"

    invalid = _proposal_payload(evidence="나는 결론부터 듣는 설명을 좋아해")
    with pytest.raises(WorkOrchestrationError, match="shareable substring"):
        IntentProposal.from_dict(invalid, request=request)


def test_authorization_blocks_capabilities_and_resources_outside_registry() -> None:
    cases = (
        ("코드를 수정해줘", "project.modify", "project"),
        ("회사 메일을 읽어줘", "account.read", "account"),
        ("바로 배포해줘", "deployment.execute", "external_service"),
    )
    for text, capability, resource in cases:
        request = WorkRequest(text)
        proposal = IntentProposal.from_dict(
            _proposal_payload(
                capability=capability,
                resource=resource,
                evidence=text,
            ),
            request=request,
        )

        brief = authorize_intent(proposal, request)

        assert brief.authorization.status == "blocked"
        with pytest.raises(WorkOrchestrationError, match="not authorized"):
            build_worker_task(brief)


def test_material_uncertainty_requires_clarification_before_worker() -> None:
    request = WorkRequest("겨울이가 너무 느린데 확인해줘")
    payload = _proposal_payload(evidence=request.user_text)
    payload["uncertainties"] = ["화면 응답과 첫 음성 중 대상이 불명확하다."]
    payload["clarification_question"] = "화면과 첫 음성 중 어느 쪽이 더 중요해?"
    proposal = IntentProposal.from_dict(payload, request=request)

    brief = authorize_intent(proposal, request)

    assert brief.authorization.status == "clarify"
    assert brief.clarification_question == "화면과 첫 음성 중 어느 쪽이 더 중요해?"
    with pytest.raises(WorkOrchestrationError, match="not authorized"):
        build_worker_task(brief)


def test_missing_question_is_repaired_to_a_safe_clarification() -> None:
    request = WorkRequest("겨울이가 너무 느린데 확인해줘")
    payload = _proposal_payload(evidence=request.user_text)
    payload["uncertainties"] = ["화면과 첫 음성 중 대상이 불명확하다."]
    payload["clarification_question"] = None
    proposal = IntentProposal.from_dict(payload, request=request)

    brief = authorize_intent(proposal, request)

    assert brief.authorization.status == "clarify"
    assert brief.clarification_question is not None
    assert "화면과 첫 음성" in brief.clarification_question


def test_worker_task_uses_only_source_tagged_shareable_context() -> None:
    request = WorkRequest(
        "Winter 저장소의 병목을 분석해줘",
        (
            WorkContext("공개 가능한 latency 문서를 본다.", "worker_shareable"),
            WorkContext("나는 결론부터 듣는 설명을 좋아해", "winter_only"),
        ),
    )
    proposal = IntentProposal.from_dict(_proposal_payload(), request=request)
    brief = authorize_intent(proposal, request)

    task = build_worker_task(brief)

    assert task.context == ("공개 가능한 latency 문서를 본다.",)
    serialized = str(task.to_dict())
    assert "결론부터 듣는 설명" not in serialized
    assert request.user_text not in serialized
    assert task.capabilities == ("project.inspect",)


def test_evidence_report_rejects_unknown_or_duplicate_ids() -> None:
    invalid_claim = json.loads(json.dumps(_report().to_dict()))
    invalid_claim["claims"] = [
        {"content": "근거 없는 주장", "evidence_ids": ["E404"]}
    ]
    with pytest.raises(WorkOrchestrationError, match="unknown evidence ids"):
        EvidenceReport.from_dict(invalid_claim)

    duplicate = json.loads(json.dumps(_report().to_dict()))
    duplicate["evidence"] = [
        {"evidence_id": "E1", "locator": "a"},
        {"evidence_id": "E1", "locator": "b"},
    ]
    with pytest.raises(WorkOrchestrationError, match="must be unique"):
        EvidenceReport.from_dict(duplicate)


def test_policy_builds_answer_or_retry_action_without_invalid_field_mix() -> None:
    brief = _authorized_brief()
    complete_report = _report()
    supported = ReviewProposal.from_dict(
        {
            "schema_version": 2,
            "worker_assessment": "supported",
            "goal_coverage": ["병목과 회귀 검사를 확인했다."],
            "judgment": "TTS부터 고치는 게 맞다.",
            "evidence_ids": ["E1"],
            "unresolved": [],
            "message": "내가 확인한 결과 TTS부터 줄이는 게 맞아.",
        },
        report=complete_report,
    )

    answer = WinterDecision.from_review(
        supported, brief=brief, report=complete_report
    )

    assert isinstance(answer.action, AnswerAction)
    assert answer.next_action == "answer"
    assert answer.follow_up_task is None

    incomplete_report = _report(check_status="not_run", unresolved=True)
    optimistic = ReviewProposal.from_dict(
        {
            "schema_version": 2,
            "worker_assessment": "supported",
            "goal_coverage": ["병목 후보는 확인했다."],
            "judgment": "측정은 부족하다.",
            "evidence_ids": ["E1"],
            "unresolved": [],
            "message": "실제 측정과 회귀 검사를 다시 수행해.",
        },
        report=incomplete_report,
    )

    retry = WinterDecision.from_review(
        optimistic, brief=brief, report=incomplete_report
    )

    assert isinstance(retry.action, RetryAction)
    assert retry.next_action == "retry"
    assert retry.final_response is None
    assert "회귀 테스트: not_run" in retry.follow_up_task


def test_review_proposal_can_only_reference_worker_evidence_ids() -> None:
    report = _report()
    payload = {
        "schema_version": 2,
        "worker_assessment": "supported",
        "goal_coverage": ["근거를 확인했다."],
        "judgment": "완료됐다.",
        "evidence_ids": ["없는 근거를 설명한 문장"],
        "unresolved": [],
        "message": "완료됐어.",
    }

    with pytest.raises(WorkOrchestrationError, match="unknown evidence ids"):
        ReviewProposal.from_dict(payload, report=report)


def test_semantic_retry_without_structured_gap_keeps_model_task() -> None:
    brief = _authorized_brief()
    report = _report()
    proposal = ReviewProposal.from_dict(
        {
            "schema_version": 2,
            "worker_assessment": "partial",
            "goal_coverage": ["병목 후보만 확인했다."],
            "judgment": "다른 구간 비교가 필요하다.",
            "evidence_ids": ["E1"],
            "unresolved": [],
            "message": "LLM과 STT 지연도 같은 방식으로 측정해.",
        },
        report=report,
    )

    decision = WinterDecision.from_review(proposal, brief=brief, report=report)

    assert decision.next_action == "retry"
    assert decision.follow_up_task is not None
    assert "LLM과 STT 지연" in decision.follow_up_task
