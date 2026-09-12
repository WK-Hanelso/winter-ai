"""Codex provider adapter that exposes only Winter's typed project gateway."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from companion.adapters.codex_control_plane import (
    control_plane_environment,
    copy_control_plane_auth,
)
from companion.adapters.codex_worker import SafeProjectBundle
from companion.work_orchestration import (
    EvidenceReport,
    WorkerTaskPacket,
    WorkOrchestrationError,
)
from companion.worker import WorkerUnavailableError
from companion.worker_gateway import (
    GatewayAuditEntry,
    GatewayDeniedError,
    GatewayEvidence,
    GatewayWorkerResult,
    parse_gateway_state,
    validate_report_evidence,
)

_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "summary",
        "evidence",
        "claims",
        "actions_performed",
        "checks",
        "limitations",
        "unresolved",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": 2},
        "summary": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["evidence_id", "locator"],
                "properties": {
                    "evidence_id": {"type": "string"},
                    "locator": {"type": "string"},
                },
            },
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["content", "evidence_ids"],
                "properties": {
                    "content": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "actions_performed": {"type": "array", "items": {"type": "string"}},
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "status", "evidence_ids"],
                "properties": {
                    "name": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["pass", "fail", "not_run"],
                    },
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
        "unresolved": {"type": "array", "items": {"type": "string"}},
    },
}

_DISABLED_FEATURES = (
    "apps",
    "apply_patch_freeform",
    "code_mode",
    "image_generation",
    "js_repl",
    "memories",
    "multi_agent",
    "multi_agent_v2",
    "plugins",
    "request_permissions_tool",
    "shell_snapshot",
    "shell_tool",
    "skill_mcp_dependency_install",
    "tool_search",
    "tool_suggest",
    "unified_exec",
    "use_agent_identity",
    "workspace_dependencies",
)
_MCP_TOOLS = ("list_files", "search_text", "read_text")


class CodexTypedProjectWorker:
    """Run Codex with provider auth in the host and project reads behind MCP."""

    def __init__(
        self,
        source_root: Path,
        *,
        codex_home: Path | None = None,
        executable: str = "codex",
        env_executable: str = "/usr/bin/env",
        model: str = "gpt-5.4",
        timeout_seconds: int = 600,
        runner: Callable[..., object] = subprocess.run,
        allowed_roots: tuple[str, ...] | None = None,
    ) -> None:
        self._source_root = source_root.resolve()
        self._codex_home = (
            codex_home.resolve()
            if codex_home is not None
            else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
        )
        self._executable = executable
        self._env_executable = env_executable
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._allowed_roots = allowed_roots

    def execute(self, packet: WorkerTaskPacket) -> GatewayWorkerResult:
        try:
            with TemporaryDirectory(prefix="winter-codex-gateway-") as temporary:
                root = Path(temporary)
                bundle = root / "bundle"
                agent_root = root / "agent"
                isolated_codex_home = root / "codex-home"
                agent_root.mkdir()
                isolated_codex_home.mkdir()
                SafeProjectBundle(
                    self._source_root, allowed_roots=self._allowed_roots,
                ).copy_to(bundle)
                copy_control_plane_auth(self._codex_home, isolated_codex_home)

                packet_path = root / "worker-task.json"
                schema_path = root / "evidence-report-schema.json"
                output_path = root / "evidence-report.json"
                state_path = root / "gateway-state.json"
                packet_path.write_text(
                    json.dumps(packet.to_dict(), ensure_ascii=False),
                    encoding="utf-8",
                )
                schema_path.write_text(
                    json.dumps(_REPORT_SCHEMA, ensure_ascii=False),
                    encoding="utf-8",
                )
                mcp_arguments = _mcp_arguments(
                    bundle=bundle,
                    packet_path=packet_path,
                    state_path=state_path,
                )
                command = self._command(
                    agent_root=agent_root,
                    schema_path=schema_path,
                    output_path=output_path,
                    mcp_arguments=mcp_arguments,
                )
                completed = self._runner(
                    command,
                    input=_worker_prompt(packet),
                    text=True,
                    capture_output=True,
                    timeout=self._timeout_seconds,
                    cwd=agent_root,
                    env=control_plane_environment(isolated_codex_home),
                    check=False,
                )
                if getattr(completed, "returncode", 1) != 0:
                    raise WorkerUnavailableError(
                        "Codex typed Worker를 사용할 수 없습니다. 인증과 CLI 상태를 확인하세요."
                    )
                report = _parse_report(output_path)
                audit, catalog = _parse_state(state_path)
                if not any(entry.decision == "allowed" for entry in audit):
                    raise WorkerUnavailableError(
                        "Codex typed Worker가 허용된 gateway audit을 남기지 않았습니다."
                    )
                try:
                    validate_report_evidence(report, catalog)
                except GatewayDeniedError as error:
                    raise WorkerUnavailableError(
                        "Codex typed Worker의 근거 검증이 실패했습니다."
                    ) from error
                return GatewayWorkerResult(
                    report=report,
                    audit_log=audit,
                    evidence_catalog=catalog,
                    worker="codex-typed-project",
                    model=self._model,
                )
        except WorkerUnavailableError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise WorkerUnavailableError(
                "Codex typed Worker 실행 환경을 준비하지 못했습니다."
            ) from error

    def _command(
        self,
        *,
        agent_root: Path,
        schema_path: Path,
        output_path: Path,
        mcp_arguments: list[str],
    ) -> list[str]:
        command = [self._executable, "exec", "-m", self._model]
        for feature in _DISABLED_FEATURES:
            command.extend(("--disable", feature))
        command.extend(
            (
                "-c",
                'approval_policy="never"',
                "-c",
                'web_search="disabled"',
                "-c",
                "tools.view_image=false",
                "-c",
                f"mcp_servers.winter_project.command={json.dumps(self._env_executable)}",
                "-c",
                f"mcp_servers.winter_project.args={json.dumps(mcp_arguments)}",
                "-c",
                f"mcp_servers.winter_project.cwd={json.dumps(str(agent_root))}",
                "-c",
                f"mcp_servers.winter_project.enabled_tools={json.dumps(list(_MCP_TOOLS))}",
                "-c",
                "mcp_servers.winter_project.env_vars=[]",
                "-c",
                'mcp_servers.winter_project.default_tools_approval_mode="auto"',
                "-c",
                "mcp_servers.winter_project.required=true",
                "-c",
                "mcp_servers.winter_project.startup_timeout_sec=10",
                "-c",
                "mcp_servers.winter_project.tool_timeout_sec=10",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--color",
                "never",
                "-C",
                str(agent_root),
                "--output-schema",
                str(schema_path),
                "-o",
                str(output_path),
                "-",
            )
        )
        return command


def _mcp_arguments(
    *,
    bundle: Path,
    packet_path: Path,
    state_path: Path,
) -> list[str]:
    source_root = Path(__file__).resolve().parents[2]
    return [
        "-i",
        "LANG=C.UTF-8",
        "PYTHONUTF8=1",
        f"PYTHONPATH={source_root}",
        sys.executable,
        "-m",
        "companion.codex_gateway_mcp",
        "--root",
        str(bundle),
        "--packet",
        str(packet_path),
        "--state",
        str(state_path),
    ]


def _worker_prompt(packet: WorkerTaskPacket) -> str:
    return (
        "You are a specialist Worker controlled by Winter. The only project data "
        "tools available to you are winter_project.list_files, search_text and "
        "read_text. Use them; do not infer file contents without issued evidence. "
        "Each call_id must be unique. Never request network, shell, account, identity, "
        "memory, conversation, credential, Human Reference or audio access. Return "
        "only the required EvidenceReport JSON. Use at most 12 project tool calls. "
        "Every claim and check must cite only evidence IDs issued by this session's "
        "tools, and every cited ID must also appear exactly once in report.evidence. "
        "Answer content in concise Korean.\n\n"
        "WorkerTaskPacket:\n"
        + json.dumps(packet.to_dict(), ensure_ascii=False, indent=2)
    )


def _parse_report(output_path: Path) -> EvidenceReport:
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        return EvidenceReport.from_dict(payload)
    except (OSError, json.JSONDecodeError, WorkOrchestrationError) as error:
        raise WorkerUnavailableError(
            "Codex typed Worker가 유효한 EvidenceReport를 반환하지 않았습니다."
        ) from error


def _parse_state(
    state_path: Path,
) -> tuple[tuple[GatewayAuditEntry, ...], tuple[GatewayEvidence, ...]]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        return parse_gateway_state(payload)
    except OSError as error:
        raise WorkerUnavailableError(
            "Codex typed Worker가 gateway audit을 남기지 않았습니다."
        ) from error
    except (json.JSONDecodeError, GatewayDeniedError) as error:
        raise WorkerUnavailableError(
            "Codex typed Worker의 gateway audit이 유효하지 않습니다."
        ) from error
