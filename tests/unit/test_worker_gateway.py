from dataclasses import replace
from pathlib import Path

import pytest

from companion.adapters.project_gateway import ProjectReadGateway
from companion.work_orchestration import (
    EvidenceReport,
    IntentProposal,
    WorkerTaskPacket,
    WorkRequest,
    authorize_intent,
    build_worker_task,
)
from companion.worker_gateway import (
    GatewayDeniedError,
    ProjectToolCall,
    validate_report_evidence,
)


def _packet() -> WorkerTaskPacket:
    request = WorkRequest("Winter 저장소의 문서를 읽어서 상태를 확인해줘")
    proposal = IntentProposal.from_dict(
        {
            "schema_version": 2,
            "objective": "Winter 저장소의 현재 상태를 확인한다.",
            "desired_outcome": "문서 근거가 있는 상태 설명을 만든다.",
            "capabilities": [
                {"kind": "project.inspect", "evidence": [request.user_text]}
            ],
            "resources": [{"kind": "project", "evidence": [request.user_text]}],
            "definition_of_done": ["문서 위치와 상태가 확인된다."],
            "uncertainties": [],
            "clarification_question": None,
        },
        request=request,
    )
    return build_worker_task(authorize_intent(proposal, request))


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("# Winter\n현재 상태는 Shadow다.\n")
    (root / "src" / "app.py").write_text("MODE = 'shadow'\n")
    return root


def test_gateway_only_accepts_authorized_project_inspection(tmp_path: Path) -> None:
    packet = _packet()
    root = _project(tmp_path)

    ProjectReadGateway(root, packet)

    with pytest.raises(GatewayDeniedError, match="capability"):
        ProjectReadGateway(
            root,
            replace(packet, capabilities=("project.modify",)),
        )
    with pytest.raises(GatewayDeniedError, match="resource"):
        ProjectReadGateway(
            root,
            replace(packet, resources=("account",)),
        )


def test_gateway_lists_searches_and_reads_with_stable_evidence(tmp_path: Path) -> None:
    gateway = ProjectReadGateway(_project(tmp_path), _packet())

    listed = gateway.execute(ProjectToolCall("C1", "list_files"))
    searched = gateway.execute(
        ProjectToolCall("C2", "search_text", query="Shadow")
    )
    first = gateway.execute(
        ProjectToolCall("C3", "read_text", path="README.md", start_line=2)
    )
    repeated = gateway.execute(
        ProjectToolCall("C4", "read_text", path="README.md", start_line=2)
    )

    assert listed.content.splitlines() == ["README.md", "src/app.py"]
    assert searched.evidence[0].locator == "README.md:2"
    assert "2: 현재 상태는 Shadow다." in first.content
    assert first.evidence[0].evidence_id == repeated.evidence[0].evidence_id
    assert first.evidence[0].locator == "README.md:2"
    assert all(entry.decision == "allowed" for entry in gateway.audit_log)


@pytest.mark.parametrize(
    ("operation", "path", "query"),
    (
        ("read_text", "/etc/passwd", None),
        ("read_text", "../secret.txt", None),
        ("write_text", "README.md", None),
        ("network.fetch", None, "https://example.com"),
    ),
)
def test_gateway_rejects_path_escape_write_and_network_operations(
    tmp_path: Path,
    operation: str,
    path: str | None,
    query: str | None,
) -> None:
    gateway = ProjectReadGateway(_project(tmp_path), _packet())

    with pytest.raises(GatewayDeniedError):
        gateway.execute(ProjectToolCall("C1", operation, path=path, query=query))

    assert gateway.audit_log[-1].decision == "denied"
    assert gateway.audit_log[-1].evidence_ids == ()


def test_gateway_rejects_symlink_hidden_binary_and_oversized_files(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    (root / ".env").write_text("SECRET=never")
    (root / "audio.txt").write_bytes(b"before\x00after")
    (root / "large.txt").write_text("x" * 512_001)
    (root / "outside.txt").symlink_to(tmp_path / "outside.txt")
    gateway = ProjectReadGateway(root, _packet())

    for path in (".env", "audio.txt", "large.txt", "outside.txt"):
        with pytest.raises(GatewayDeniedError):
            gateway.execute(ProjectToolCall(path, "read_text", path=path))


def test_gateway_fails_closed_if_snapshot_changes_during_execution(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    gateway = ProjectReadGateway(root, _packet())
    (root / "README.md").write_text("changed after authorization\n")

    with pytest.raises(GatewayDeniedError, match="project_snapshot_changed"):
        gateway.execute(ProjectToolCall("C1", "read_text", path="README.md"))

    assert gateway.audit_log[-1].reason == "project_snapshot_changed"


def test_worker_report_can_only_use_evidence_issued_by_gateway(tmp_path: Path) -> None:
    gateway = ProjectReadGateway(_project(tmp_path), _packet())
    result = gateway.execute(
        ProjectToolCall("C1", "read_text", path="README.md", start_line=2)
    )
    issued = result.evidence[0]
    payload: dict[str, object] = {
        "schema_version": 2,
        "summary": "현재 상태를 확인했다.",
        "evidence": [
            {"evidence_id": issued.evidence_id, "locator": issued.locator}
        ],
        "claims": [
            {
                "content": "현재 상태는 Shadow다.",
                "evidence_ids": [issued.evidence_id],
            }
        ],
        "actions_performed": ["README를 읽었다."],
        "checks": [],
        "limitations": [],
        "unresolved": [],
    }
    report = EvidenceReport.from_dict(payload)

    validate_report_evidence(report, gateway.evidence_catalog)

    forged = dict(payload)
    forged["evidence"] = [
        {"evidence_id": issued.evidence_id, "locator": "/etc/passwd:1"}
    ]
    with pytest.raises(GatewayDeniedError, match="outside the gateway session"):
        validate_report_evidence(
            EvidenceReport.from_dict(forged), gateway.evidence_catalog
        )
