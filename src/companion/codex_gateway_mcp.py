"""Minimal stdio MCP server exposing Winter's typed project read gateway."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Any, cast

from companion.adapters.project_gateway import ProjectReadGateway
from companion.work_orchestration import WorkerTaskPacket
from companion.worker_gateway import (
    GatewayDeniedError,
    GatewayToolResult,
    ProjectToolCall,
    serialize_gateway_state,
)

_TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": "list_files",
        "title": "List authorized project files",
        "description": "List safe text files in the fixed project snapshot.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"call_id": {"type": "string"}},
            "required": ["call_id"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "search_text",
        "title": "Search authorized project text",
        "description": "Search literal text in the fixed project snapshot.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "call_id": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["call_id", "query"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "read_text",
        "title": "Read authorized project text",
        "description": "Read at most 200 numbered lines from one authorized text file.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "call_id": {"type": "string"},
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["call_id", "path"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
)
_SAFE_ENVIRONMENT_KEYS = frozenset({"LANG", "LC_CTYPE", "PYTHONPATH", "PYTHONUTF8"})


class ProjectGatewayMcpServer:
    def __init__(
        self,
        root: Path,
        packet: WorkerTaskPacket,
        state_path: Path,
    ) -> None:
        self._gateway = ProjectReadGateway(root, packet)
        self._state_path = state_path
        self._persist()

    def handle(self, message: object) -> dict[str, object] | None:
        data = _mapping(message, "JSON-RPC message")
        if data.get("jsonrpc") != "2.0":
            return _error(data.get("id"), -32600, "invalid JSON-RPC version")
        method = data.get("method")
        if not isinstance(method, str):
            return _error(data.get("id"), -32600, "method is required")
        if "id" not in data:
            return None
        request_id = data["id"]
        if method == "initialize":
            params = _mapping(data.get("params", {}), "initialize params")
            requested = params.get("protocolVersion", "2025-06-18")
            version = requested if isinstance(requested, str) else "2025-06-18"
            return _result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "winter-project-gateway", "version": "0.1.0"},
                },
            )
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": list(_TOOLS)})
        if method == "tools/call":
            return self._call_tool(request_id, data.get("params", {}))
        return _error(request_id, -32601, "method not found")

    def _call_tool(self, request_id: object, params_value: object) -> dict[str, object]:
        try:
            params = _mapping(params_value, "tool call params")
            name = _required_string(params.get("name"), "tool name")
            arguments = _mapping(params.get("arguments", {}), "tool arguments")
            call = ProjectToolCall(
                call_id=_required_string(arguments.get("call_id"), "call_id"),
                operation=name,
                path=_optional_string(arguments.get("path"), "path"),
                query=_optional_string(arguments.get("query"), "query"),
                start_line=_optional_integer(arguments.get("start_line"), "start_line"),
                end_line=_optional_integer(arguments.get("end_line"), "end_line"),
            )
            tool_result = self._gateway.execute(call)
        except (GatewayDeniedError, ValueError) as error:
            self._persist()
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(error)}],
                    "isError": True,
                },
            )
        self._persist()
        rendered = _render_tool_result(tool_result)
        return _result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(rendered, ensure_ascii=False),
                    }
                ],
                "structuredContent": rendered,
                "isError": False,
            },
        )

    def _persist(self) -> None:
        self._state_path.write_text(
            serialize_gateway_state(self._gateway),
            encoding="utf-8",
        )


def _render_tool_result(result: GatewayToolResult) -> dict[str, object]:
    return {
        "call_id": result.call_id,
        "operation": result.operation,
        "content": result.content,
        "evidence": [asdict(item) for item in result.evidence],
    }


def _result(request_id: object, result: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, Any], value)


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name)


def _optional_integer(value: object, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _assert_sanitized_environment() -> None:
    unexpected = set(os.environ) - _SAFE_ENVIRONMENT_KEYS
    if unexpected:
        raise RuntimeError(
            "MCP gateway received unexpected control-plane environment keys: "
            + ", ".join(sorted(unexpected))
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    arguments = parser.parse_args()
    _assert_sanitized_environment()
    packet = WorkerTaskPacket.from_dict(
        json.loads(arguments.packet.read_text(encoding="utf-8"))
    )
    server = ProjectGatewayMcpServer(arguments.root, packet, arguments.state)
    for line in sys.stdin:
        try:
            response = server.handle(json.loads(line))
        except (json.JSONDecodeError, ValueError) as error:
            response = _error(None, -32700, str(error))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
