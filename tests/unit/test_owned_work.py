from pathlib import Path

import pytest

from companion.owned_work import (
    ReadOnlyProjectCanaryPolicy,
    WinterOwnedProjectCanary,
)
from companion.work_orchestration import (
    AnswerAction,
    EvidenceReport,
    IntentBrief,
    WinterDecision,
    WorkAuthorization,
    WorkRequest,
)
from companion.worker import WorkerUnavailableError
from companion.worker_gateway import (
    GatewayAuditEntry,
    GatewayEvidence,
    GatewayWorkerResult,
)


class _Executive:
    def __init__(self) -> None:
        self.requests: list[WorkRequest] = []
        self.reports: list[EvidenceReport] = []

    def create_brief(self, request: WorkRequest) -> IntentBrief:
        self.requests.append(request)
        return IntentBrief(
            schema_version=2,
            objective="응답 지연의 가장 큰 구간을 찾는다.",
            desired_outcome="근거가 있는 개선 한 가지를 정한다.",
            capabilities=("project.inspect",),
            resources=("project",),
            definition_of_done=("병목과 개선 한 가지를 보고한다.",),
            uncertainties=(),
            worker_context=(),
            authorization=WorkAuthorization("ready", (), None),
        )

    def review(
        self, brief: IntentBrief, report: EvidenceReport
    ) -> WinterDecision:
        self.reports.append(report)
        return WinterDecision(
            schema_version=2,
            worker_assessment="supported",
            goal_coverage=("병목과 개선을 확인했다.",),
            judgment="STT 이후 구간을 먼저 나눠 측정하는 게 맞아.",
            evidence_ids=("E1",),
            unresolved=(),
            action=AnswerAction(
                "STT 이후 구간을 먼저 나눠 측정하는 게 맞아."
            ),
        )


class _Worker:
    def __init__(self, source: Path, *, mutate: bool = False) -> None:
        self.source = source
        self.mutate = mutate
        self.packets = []

    def execute(self, packet):  # type: ignore[no-untyped-def]
        self.packets.append(packet)
        if self.mutate:
            (self.source / "README.md").write_text("changed\n", encoding="utf-8")
        report = EvidenceReport.from_dict(
            {
                "schema_version": 2,
                "summary": "STT 이후 구간이 더 크다.",
                "evidence": [
                    {"evidence_id": "E1", "locator": "docs/realtime.md:10"}
                ],
                "claims": [
                    {
                        "content": "STT 이후 구간이 더 크다.",
                        "evidence_ids": ["E1"],
                    }
                ],
                "actions_performed": ["문서와 코드를 읽었다."],
                "checks": [
                    {"name": "근거", "status": "pass", "evidence_ids": ["E1"]}
                ],
                "limitations": ["실제 표본은 네 턴뿐이야."],
                "unresolved": [],
            }
        )
        return GatewayWorkerResult(
            report=report,
            audit_log=(GatewayAuditEntry("C1", "read_text", "allowed", "ok", ("E1",)),),
            evidence_catalog=(
                GatewayEvidence("E1", "docs/realtime.md:10", "a" * 64),
            ),
            worker="codex-typed-project",
            model="gpt-5.4",
        )


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Winter\n", encoding="utf-8")
    return source


def test_policy_routes_only_clear_read_only_project_inspection() -> None:
    policy = ReadOnlyProjectCanaryPolicy()

    routed = policy.route(
        "겨울이의 실제 대답 지연에서 어느 구간이 가장 큰지 현재 코드와 "
        "저장된 latency 근거로 진단하고 개선 하나를 추천해줘"
    )

    assert routed is not None
    assert policy.route("오늘 운동이 너무 힘들었어") is None
    assert policy.route("이 코드를 고쳐줘") is None
    assert policy.route("내 대화 기록과 코드를 분석해줘") is None


def test_canary_sends_model_brief_not_raw_conversation_and_returns_winter_review(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    executive = _Executive()
    worker = _Worker(source)
    canary = WinterOwnedProjectCanary(source, executive, worker)  # type: ignore[arg-type]
    request = WorkRequest("내가 말한 원문을 Worker에 그대로 보내지 말고 코드 원인을 분석해줘")

    result = canary.execute(request)

    assert executive.requests == [request]
    assert len(worker.packets) == 1
    packet = worker.packets[0]
    assert packet.objective == "응답 지연의 가장 큰 구간을 찾는다."
    assert request.user_text not in str(packet.to_dict())
    assert packet.capabilities == ("project.inspect",)
    assert result.answer == "STT 이후 구간을 먼저 나눠 측정하는 게 맞아."
    assert result.evidence == ("docs/realtime.md:10",)
    assert result.limitations == ("실제 표본은 네 턴뿐이야.",)
    assert (source / "README.md").read_text(encoding="utf-8") == "# Winter\n"


def test_canary_detects_any_source_change_during_read_only_worker(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    canary = WinterOwnedProjectCanary(
        source,
        _Executive(),  # type: ignore[arg-type]
        _Worker(source, mutate=True),  # type: ignore[arg-type]
    )

    with pytest.raises(WorkerUnavailableError, match="source가 달라졌습니다"):
        canary.execute(WorkRequest("코드 원인을 분석해줘"))


def test_canary_fails_closed_when_project_baseline_is_unavailable(
    tmp_path: Path,
) -> None:
    empty_source = tmp_path / "empty"
    empty_source.mkdir()
    canary = WinterOwnedProjectCanary(
        empty_source,
        _Executive(),  # type: ignore[arg-type]
        _Worker(empty_source),  # type: ignore[arg-type]
    )

    with pytest.raises(WorkerUnavailableError, match="기준선을 만들지 못했습니다"):
        canary.execute(WorkRequest("코드 원인을 분석해줘"))
