"""Codex implementation Worker confined to a disposable sanitized copy."""

from __future__ import annotations

import ast
from collections.abc import Callable
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from companion.adapters.codex_control_plane import (
    control_plane_environment,
    copy_control_plane_auth,
)
from companion.adapters.codex_worker import SafeProjectBundle
from companion.isolated_work import (
    ChangedFile,
    ImplementationAuthorization,
    IsolatedWorkResult,
    VerificationResult,
)
from companion.project_baseline import build_project_baseline
from companion.verification_sandbox import (
    BubblewrapVerificationSandbox,
    VerificationSandboxError,
)
from companion.worker import WorkerUnavailableError

_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "limitations"],
    "properties": {
        "summary": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
}
_DISABLED_FEATURES = (
    "apps",
    "code_mode",
    "image_generation",
    "js_repl",
    "memories",
    "multi_agent",
    "multi_agent_v2",
    "plugins",
    "request_permissions_tool",
    "skill_mcp_dependency_install",
    "tool_search",
    "tool_suggest",
    "use_agent_identity",
    "workspace_dependencies",
)
_IGNORED_ARTIFACT_PARTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}
_IGNORED_ARTIFACT_PATHS = {".codex"}
_MAX_CHANGED_FILE_BYTES = 512_000
_MAX_PATCH_BYTES = 1_000_000
_MAX_VERIFICATION_OUTPUT = 20_000


class CodexIsolatedWorkWorker:
    """Generate and verify a patch without exposing or changing the source tree."""

    def __init__(
        self,
        source_root: Path,
        *,
        codex_home: Path | None = None,
        executable: str = "codex",
        model: str = "gpt-5.4",
        timeout_seconds: int = 600,
        verification_timeout_seconds: int = 120,
        runner: Callable[..., object] = subprocess.run,
        verification_runner: Callable[..., object] = subprocess.run,
        python_executable: str = sys.executable,
        verification_sandbox_executable: str = "bwrap",
    ) -> None:
        self._source_root = source_root.resolve()
        self._codex_home = (
            codex_home.resolve()
            if codex_home is not None
            else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
        )
        self._executable = shutil.which(executable) or executable
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._python_executable = python_executable
        self._verification_sandbox = BubblewrapVerificationSandbox(
            sandbox_executable=verification_sandbox_executable,
            timeout_seconds=verification_timeout_seconds,
            runner=verification_runner,
        )

    def execute(self, authorization: ImplementationAuthorization) -> IsolatedWorkResult:
        try:
            with TemporaryDirectory(prefix="winter-codex-isolated-") as temporary:
                root = Path(temporary)
                baseline = root / "baseline"
                workspace = root / "workspace"
                isolated_codex_home = root / "codex-home"
                isolated_agent_home = root / "agent-home"
                isolated_codex_home.mkdir()
                isolated_agent_home.mkdir()
                SafeProjectBundle(self._source_root).copy_to(baseline)
                project_baseline = build_project_baseline(baseline)
                shutil.copytree(baseline, workspace)
                copy_control_plane_auth(self._codex_home, isolated_codex_home)

                schema_path = root / "implementation-report-schema.json"
                output_path = root / "implementation-report.json"
                schema_path.write_text(
                    json.dumps(_REPORT_SCHEMA, ensure_ascii=False), encoding="utf-8"
                )
                tool_path = _prepare_matching_sandbox_launcher(root, self._executable)
                command = self._command(
                    workspace=workspace,
                    schema_path=schema_path,
                    output_path=output_path,
                )
                environment = control_plane_environment(isolated_codex_home)
                environment["HOME"] = str(isolated_agent_home)
                environment["PATH"] = (
                    f"{tool_path}{os.pathsep}{Path(self._executable).parent}"
                    f"{os.pathsep}{os.defpath}"
                )
                completed = self._runner(
                    command,
                    input=_worker_prompt(authorization),
                    text=True,
                    capture_output=True,
                    timeout=self._timeout_seconds,
                    cwd=workspace,
                    env=environment,
                    check=False,
                )
                if getattr(completed, "returncode", 1) != 0:
                    detail = _execution_failure_detail(completed)
                    raise WorkerUnavailableError(
                        "Codex 격리 Worker를 사용할 수 없습니다. 인증과 CLI 상태를 확인하세요."
                        + (f" 원인: {detail}" if detail else "")
                    )
                summary, limitations = _parse_report(output_path)
                changed_files, patch = _build_scoped_patch(
                    baseline,
                    workspace,
                    authorization.allowed_paths,
                    authorization.max_changed_lines,
                )
                if not changed_files:
                    detail = summary
                    if limitations:
                        detail += " / " + "; ".join(limitations)
                    raise WorkerUnavailableError(
                        "Codex 격리 Worker가 실제 변경을 만들지 않았습니다. "
                        "Worker 보고: "
                        + detail[:1_000]
                    )
                test_verification = self._verify(
                    workspace, authorization.verification_profile
                )
                structure_verification = _verify_python_structure(
                    workspace, changed_files
                )
                verifications = (test_verification, structure_verification)
                status = (
                    "awaiting_adoption"
                    if all(result.status == "pass" for result in verifications)
                    else "verification_failed"
                )
                proposal_id = "proposal-" + hashlib.sha256(
                    (authorization.authorization_id + "\n" + patch).encode("utf-8")
                ).hexdigest()[:16]
                return IsolatedWorkResult(
                    proposal_id=proposal_id,
                    authorization_id=authorization.authorization_id,
                    status=status,
                    summary=summary,
                    changed_files=changed_files,
                    patch=patch,
                    verifications=verifications,
                    limitations=limitations,
                    worker="codex-isolated-work",
                    model=self._model,
                    project_baseline=project_baseline,
                )
        except WorkerUnavailableError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise WorkerUnavailableError(
                "Codex 격리 Worker 실행 환경을 준비하지 못했습니다."
            ) from error

    def _command(
        self,
        *,
        workspace: Path,
        schema_path: Path,
        output_path: Path,
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
                "sandbox_workspace_write.network_access=false",
                "-c",
                'shell_environment_policy.inherit="core"',
                "-c",
                'shell_environment_policy.include_only=["PATH","LANG","LC_ALL","PYTHONUTF8"]',
                "-c",
                'shell_environment_policy.set.LANG="C.UTF-8"',
                "-c",
                'shell_environment_policy.set.PYTHONUTF8="1"',
                "--ephemeral",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--json",
                "-C",
                str(workspace),
                "--output-schema",
                str(schema_path),
                "-o",
                str(output_path),
                "-",
            )
        )
        return command

    def _verify(self, workspace: Path, profile: str) -> VerificationResult:
        if profile != "python_unittest":
            raise WorkerUnavailableError("등록되지 않은 독립 검증 profile입니다.")
        if not self._verification_sandbox.available:
            raise WorkerUnavailableError(
                "독립 검증 sandbox가 없어 Worker 코드를 실행하지 않았습니다."
            )
        try:
            completed = self._verification_sandbox.run(
                workspace,
                (
                    self._python_executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                ),
            )
        except VerificationSandboxError as error:
            raise WorkerUnavailableError(str(error)) from error
        exit_code = int(getattr(completed, "returncode", 1))
        output = (
            str(getattr(completed, "stdout", ""))
            + str(getattr(completed, "stderr", ""))
        )[-_MAX_VERIFICATION_OUTPUT:]
        return VerificationResult(
            name=profile,
            status="pass" if exit_code == 0 else "fail",
            exit_code=exit_code,
            output=output,
        )


def _worker_prompt(authorization: ImplementationAuthorization) -> str:
    allowed = "\n".join(f"- {path}" for path in authorization.allowed_paths)
    return (
        "You are an implementation Worker controlled by Winter. Work only inside "
        "the disposable sanitized current workspace. You may inspect files, edit "
        "only the exact authorized paths below, and run local tests. Never use "
        "network, accounts, credentials, hidden files, personal memory, conversation "
        "data, audio, or paths outside this workspace. Do not delete files. Keep the "
        "change minimal. The host independently checks the diff and reruns a fixed "
        "verification profile; your claims are not trusted as proof. Return only the "
        "required JSON report.\n\n"
        "Write summary and limitations in concise Korean.\n\n"
        f"Objective:\n{authorization.objective}\n\n"
        f"Exact writable paths:\n{allowed}\n\n"
        f"Maximum changed lines: {authorization.max_changed_lines}\n"
        f"Host verification profile: {authorization.verification_profile}"
    )


def _parse_report(output_path: Path) -> tuple[str, tuple[str, ...]]:
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        summary = str(payload["summary"]).strip()
        limitations = tuple(
            value
            for value in (str(item).strip() for item in payload["limitations"])
            if value
        )
        if not summary:
            raise ValueError("empty summary")
        return summary, limitations
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise WorkerUnavailableError(
            "Codex 격리 Worker가 유효한 구현 보고서를 반환하지 않았습니다."
        ) from error


def _execution_failure_detail(completed: object) -> str:
    for line in reversed(str(getattr(completed, "stdout", "")).splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "error" and isinstance(payload.get("message"), str):
            return str(payload["message"])[:500]
    stderr_lines = str(getattr(completed, "stderr", "")).strip().splitlines()
    return stderr_lines[-1][:500] if stderr_lines else ""


def _prepare_matching_sandbox_launcher(root: Path, executable: str) -> Path:
    """Put the native sandbox launcher matching this Codex install on PATH."""

    resolved = Path(executable).resolve()
    candidates: list[Path]
    if resolved.suffix == ".js" and resolved.parent.name == "bin":
        package_root = resolved.parent.parent
        candidates = sorted(
            package_root.glob(
                "node_modules/@openai/codex-*/vendor/*/codex/codex"
            )
        )
    else:
        candidates = [resolved]
    native = next(
        (candidate for candidate in candidates if candidate.is_file()), None
    )
    if native is None:
        return Path(os.defpath.split(os.pathsep)[0])
    tool_bin = root / "tool-bin"
    tool_bin.mkdir()
    (tool_bin / "codex-linux-sandbox").symlink_to(native)
    return tool_bin


def _verify_python_structure(
    workspace: Path, changed_files: tuple[ChangedFile, ...]
) -> VerificationResult:
    problems: list[str] = []
    for change in changed_files:
        if not change.path.endswith(".py"):
            continue
        path = workspace / change.path
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=change.path)
        except (OSError, SyntaxError, UnicodeError) as error:
            problems.append(f"{change.path}: {error}")
            continue
        names: set[str] = set()
        duplicates: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)):
                if node.name in names:
                    duplicates.add(node.name)
                names.add(node.name)
        if duplicates:
            problems.append(
                f"{change.path}: duplicate top-level definitions: "
                + ", ".join(sorted(duplicates))
            )
    return VerificationResult(
        name="python_structure",
        status="fail" if problems else "pass",
        exit_code=1 if problems else 0,
        output="\n".join(problems) if problems else "Python structure check passed.",
    )


def _build_scoped_patch(
    baseline: Path,
    workspace: Path,
    allowed_paths: tuple[str, ...],
    max_changed_lines: int,
) -> tuple[tuple[ChangedFile, ...], str]:
    before = _snapshot(baseline)
    after = _snapshot(workspace)
    deleted = sorted(set(before) - set(after))
    if deleted:
        raise WorkerUnavailableError(
            "격리 Worker가 파일 삭제를 시도했습니다: " + ", ".join(deleted)
        )
    changed_paths = sorted(
        path for path in after if path not in before or after[path] != before[path]
    )
    if not changed_paths:
        return (), ""
    unauthorized = [path for path in changed_paths if path not in allowed_paths]
    if unauthorized:
        raise WorkerUnavailableError(
            "격리 Worker가 허용 범위 밖의 파일을 변경했습니다: "
            + ", ".join(unauthorized)
        )

    file_changes: list[ChangedFile] = []
    patch_parts: list[str] = []
    for path in changed_paths:
        before_bytes = before.get(path)
        after_bytes = after[path]
        before_text = before_bytes.decode("utf-8") if before_bytes is not None else ""
        after_text = after_bytes.decode("utf-8")
        patch_parts.extend(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        file_changes.append(
            ChangedFile(
                path=path,
                change_type="modified" if before_bytes is not None else "added",
                before_sha256=(
                    hashlib.sha256(before_bytes).hexdigest()
                    if before_bytes is not None
                    else None
                ),
                after_sha256=hashlib.sha256(after_bytes).hexdigest(),
            )
        )
    patch = "".join(patch_parts)
    if len(patch.encode("utf-8")) > _MAX_PATCH_BYTES:
        raise WorkerUnavailableError("격리 Worker 변경 patch가 안전 제한을 초과했습니다.")
    changed_line_count = sum(
        1
        for line in patch.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )
    if changed_line_count > max_changed_lines:
        raise WorkerUnavailableError(
            "격리 Worker 변경이 승인된 line 예산을 초과했습니다: "
            f"{changed_line_count}/{max_changed_lines}"
        )
    return tuple(file_changes), patch


def _snapshot(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root)
        if relative.as_posix() in _IGNORED_ARTIFACT_PATHS:
            continue
        if any(part in _IGNORED_ARTIFACT_PARTS for part in relative.parts):
            continue
        if candidate.is_symlink():
            raise WorkerUnavailableError("격리 Worker가 symlink를 만들었습니다.")
        if not candidate.is_file():
            continue
        if any(part.startswith(".") for part in relative.parts):
            raise WorkerUnavailableError(
                "격리 Worker가 숨김 파일을 만들었습니다: " + relative.as_posix()
            )
        data = candidate.read_bytes()
        if len(data) > _MAX_CHANGED_FILE_BYTES:
            raise WorkerUnavailableError("격리 Worker 파일이 안전 크기 제한을 초과했습니다.")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkerUnavailableError("격리 Worker가 binary 파일을 만들었습니다.") from error
        snapshot[relative.as_posix()] = data
    return snapshot
