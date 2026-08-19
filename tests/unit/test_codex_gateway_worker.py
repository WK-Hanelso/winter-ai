import json
import os
from pathlib import Path
from typing import Any

import pytest

from companion.adapters.codex_gateway_worker import CodexTypedProjectWorker
from companion.adapters.project_gateway import ProjectReadGateway
from companion.work_orchestration import EvidenceReport, WorkerTaskPacket
from companion.worker import WorkerUnavailableError
from companion.worker_gateway import ProjectToolCall, serialize_gateway_state


def _packet() -> WorkerTaskPacket:
    return WorkerTaskPacket(
        schema_version=2,
        objective="README의 현재 상태를 확인한다.",
        desired_outcome="파일 근거가 있는 요약을 만든다.",
        capabilities=("project.inspect",),
        resources=("project",),
        context=("README를 우선 확인한다.",),
        definition_of_done=("상태와 근거 위치를 보고한다.",),
        prohibited_actions=("네트워크와 쓰기를 사용하지 않는다.",),
        required_report=("summary", "evidence"),
    )


def _config(command: list[str], key: str) -> object:
    prefix = f"{key}="
    for index, value in enumerate(command):
        if value == "-c" and command[index + 1].startswith(prefix):
            return json.loads(command[index + 1][len(prefix) :])
    raise AssertionError(f"missing config: {key}")


def _write_fake_result(command: list[str], *, forged: bool = False) -> None:
    mcp_arguments = _config(command, "mcp_servers.winter_project.args")
    assert isinstance(mcp_arguments, list)
    packet_path = Path(mcp_arguments[mcp_arguments.index("--packet") + 1])
    state_path = Path(mcp_arguments[mcp_arguments.index("--state") + 1])
    workspace = Path(mcp_arguments[mcp_arguments.index("--root") + 1])
    packet_payload = json.loads(packet_path.read_text())
    packet = WorkerTaskPacket.from_dict(packet_payload)
    gateway = ProjectReadGateway(workspace, packet)
    result = gateway.execute(
        ProjectToolCall("C1", "read_text", path="README.md", start_line=1)
    )
    state_path.write_text(serialize_gateway_state(gateway))
    issued = result.evidence[0]
    locator = "/etc/passwd:1" if forged else issued.locator
    report = {
        "schema_version": 2,
        "summary": "README를 확인했다.",
        "evidence": [{"evidence_id": issued.evidence_id, "locator": locator}],
        "claims": [
            {
                "content": "Winter 문서가 있다.",
                "evidence_ids": [issued.evidence_id],
            }
        ],
        "actions_performed": ["README 첫 줄을 읽었다."],
        "checks": [
            {
                "name": "근거 확인",
                "status": "pass",
                "evidence_ids": [issued.evidence_id],
            }
        ],
        "limitations": [],
        "unresolved": [],
    }
    output_path = Path(command[command.index("-o") + 1])
    output_path.write_text(json.dumps(report, ensure_ascii=False))


def test_codex_worker_separates_control_plane_from_typed_agent_tools(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Winter\n")
    codex_home = tmp_path / "real-codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"private":"credential"}')
    captured: dict[str, Any] = {}

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        captured["command"] = command
        captured["env"] = kwargs["env"]
        isolated_home = Path(kwargs["env"]["CODEX_HOME"])  # type: ignore[index]
        assert isolated_home != codex_home
        assert (isolated_home / "auth.json").read_text() == '{"private":"credential"}'
        _write_fake_result(command)

        class Completed:
            returncode = 0
            stderr = ""

        return Completed()

    worker = CodexTypedProjectWorker(
        source,
        codex_home=codex_home,
        executable="codex",
        runner=run_process,
    )

    result = worker.execute(_packet())

    command = captured["command"]
    disabled = {
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--disable"
    }
    assert {"shell_tool", "unified_exec", "shell_snapshot", "plugins", "apps"} <= disabled
    assert _config(command, "web_search") == "disabled"
    assert _config(command, "tools.view_image") is False
    assert _config(command, "approval_policy") == "never"
    assert _config(command, "mcp_servers.winter_project.enabled_tools") == [
        "list_files",
        "search_text",
        "read_text",
    ]
    assert _config(command, "mcp_servers.winter_project.command") == "/usr/bin/env"
    mcp_arguments = _config(command, "mcp_servers.winter_project.args")
    assert mcp_arguments[0] == "-i"
    assert not any(
        "CODEX_HOME" in value or "auth.json" in value
        for value in mcp_arguments
    )
    parent_env = captured["env"]
    assert "CODEX_HOME" in parent_env
    round_trip = json.loads(json.dumps(result.report.to_dict()))
    assert result.report == EvidenceReport.from_dict(round_trip)
    assert result.audit_log[0].decision == "allowed"
    assert result.evidence_catalog[0].locator == "README.md:1"


def test_codex_worker_rejects_forged_report_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Winter\n")

    def run_process(*args: object, **kwargs: object) -> object:
        _write_fake_result(list(args[0]), forged=True)  # type: ignore[arg-type]

        class Completed:
            returncode = 0
            stderr = ""

        return Completed()

    worker = CodexTypedProjectWorker(source, runner=run_process)

    with pytest.raises(WorkerUnavailableError, match="근거 검증"):
        worker.execute(_packet())


def test_codex_worker_does_not_forward_ambient_secrets_to_mcp_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Winter\n")
    monkeypatch.setenv("OPENAI_API_KEY", "never-forward-this")
    monkeypatch.setenv("WINTER_PRIVATE_TOKEN", "never-forward-this-either")
    captured: dict[str, object] = {}

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        captured["command"] = command
        _write_fake_result(command)

        class Completed:
            returncode = 0
            stderr = ""

        return Completed()

    CodexTypedProjectWorker(source, runner=run_process).execute(_packet())

    rendered = json.dumps(captured["command"])
    assert "never-forward-this" not in rendered
    assert _config(captured["command"], "mcp_servers.winter_project.env_vars") == []
    assert "OPENAI_API_KEY" in os.environ


def test_codex_worker_fails_without_a_gateway_audit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Winter\n")

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "summary": "도구 없이 답했다.",
                    "evidence": [],
                    "claims": [],
                    "actions_performed": [],
                    "checks": [],
                    "limitations": ["근거 없음"],
                    "unresolved": [],
                }
            )
        )

        class Completed:
            returncode = 0
            stderr = ""

        return Completed()

    worker = CodexTypedProjectWorker(source, runner=run_process)

    with pytest.raises(WorkerUnavailableError, match="audit"):
        worker.execute(_packet())
