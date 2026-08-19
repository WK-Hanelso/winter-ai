import json
from pathlib import Path
import subprocess
import sys

from companion.codex_gateway_mcp import ProjectGatewayMcpServer
from companion.work_orchestration import WorkerTaskPacket


def _packet() -> WorkerTaskPacket:
    return WorkerTaskPacket(
        schema_version=2,
        objective="README의 현재 상태를 확인한다.",
        desired_outcome="파일 근거가 있는 요약을 만든다.",
        capabilities=("project.inspect",),
        resources=("project",),
        context=(),
        definition_of_done=("상태와 근거 위치를 보고한다.",),
        prohibited_actions=("네트워크와 쓰기를 사용하지 않는다.",),
        required_report=("summary", "evidence"),
    )


def _request(request_id: int, method: str, params: object | None = None) -> dict[str, object]:
    message: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def test_mcp_exposes_only_typed_project_read_tools_and_persists_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "README.md").write_text("# Winter\n현재 상태는 Shadow다.\n")
    state_path = tmp_path / "gateway-state.json"
    server = ProjectGatewayMcpServer(root, _packet(), state_path)

    initialized = server.handle(
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
    )
    listed = server.handle(_request(2, "tools/list", {}))
    called = server.handle(
        _request(
            3,
            "tools/call",
            {
                "name": "read_text",
                "arguments": {
                    "call_id": "C1",
                    "path": "README.md",
                    "start_line": 2,
                    "end_line": 2,
                },
            },
        )
    )

    assert initialized is not None
    assert initialized["result"]["capabilities"] == {  # type: ignore[index]
        "tools": {"listChanged": False}
    }
    assert listed is not None
    names = {
        tool["name"]
        for tool in listed["result"]["tools"]  # type: ignore[index,union-attr]
    }
    assert names == {"list_files", "search_text", "read_text"}
    assert called is not None
    structured = called["result"]["structuredContent"]  # type: ignore[index]
    assert structured["content"] == "2: 현재 상태는 Shadow다."
    assert structured["evidence"][0]["locator"] == "README.md:2"

    persisted = json.loads(state_path.read_text())
    assert persisted["audit_log"] == [
        {
            "call_id": "C1",
            "operation": "read_text",
            "decision": "allowed",
            "reason": "policy_allowed",
            "evidence_ids": [structured["evidence"][0]["evidence_id"]],
        }
    ]
    assert persisted["evidence_catalog"][0]["locator"] == "README.md:2"


def test_mcp_routes_unlisted_tool_calls_through_fail_closed_gateway(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "README.md").write_text("safe\n")
    state_path = tmp_path / "gateway-state.json"
    server = ProjectGatewayMcpServer(root, _packet(), state_path)

    denied = server.handle(
        _request(
            1,
            "tools/call",
            {
                "name": "network.fetch",
                "arguments": {"call_id": "C-denied", "query": "https://example.com"},
            },
        )
    )

    assert denied is not None
    assert denied["result"]["isError"] is True  # type: ignore[index]
    persisted = json.loads(state_path.read_text())
    assert persisted["audit_log"][0]["decision"] == "denied"
    assert persisted["audit_log"][0]["reason"] == "operation_not_allowed"


def test_mcp_notifications_do_not_emit_json_rpc_responses(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "README.md").write_text("safe\n")
    server = ProjectGatewayMcpServer(
        root,
        _packet(),
        tmp_path / "gateway-state.json",
    )

    assert server.handle(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    ) is None


def test_stdio_mcp_starts_with_an_empty_control_plane_environment(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "README.md").write_text("# Winter\n")
    packet_path = tmp_path / "packet.json"
    state_path = tmp_path / "state.json"
    packet_path.write_text(json.dumps(_packet().to_dict()))
    source_root = Path(__file__).resolve().parents[2] / "src"
    messages = (
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        ),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _request(2, "tools/list", {}),
        _request(
            3,
            "tools/call",
            {
                "name": "read_text",
                "arguments": {"call_id": "C1", "path": "README.md"},
            },
        ),
    )
    completed = subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            "LANG=C.UTF-8",
            "PYTHONUTF8=1",
            f"PYTHONPATH={source_root}",
            sys.executable,
            "-m",
            "companion.codex_gateway_mcp",
            "--root",
            str(root),
            "--packet",
            str(packet_path),
            "--state",
            str(state_path),
        ],
        input="".join(json.dumps(message) + "\n" for message in messages),
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    responses = tuple(json.loads(line) for line in completed.stdout.splitlines())
    assert tuple(response["id"] for response in responses) == (1, 2, 3)
    assert responses[2]["result"]["isError"] is False
    persisted = json.loads(state_path.read_text())
    assert persisted["audit_log"][0]["decision"] == "allowed"
