"""OpenAI Codex CLI adapter with an explicit read-only project boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from companion.worker import (
    ReadOnlyWorkerRequest,
    ReadOnlyWorkerResult,
    WorkerUnavailableError,
)

_ROOT_FILES = {
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "winter",
}
_ROOT_PREFIXES = ("Dockerfile", "compose.")
_ALLOWED_DIRECTORIES = {
    "configs",
    "deploy",
    "docs",
    "experiments",
    "scripts",
    "src",
    "tests",
}
_ALLOWED_SUFFIXES = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_DENIED_PARTS = {
    ".agents",
    ".claude",
    ".codex",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "checkpoints",
    "data",
    "datasets",
    "generated_audio",
    "human_reference",
    "models",
    "node_modules",
    "review",
    "voice_references",
}
_MAX_FILE_BYTES = 512_000
_MAX_BUNDLE_BYTES = 12_000_000
_MAX_FILES = 1_500

_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "evidence", "limitations"],
    "properties": {
        "answer": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass(frozen=True)
class BundleManifest:
    relative_paths: tuple[str, ...]
    total_bytes: int


class SafeProjectBundle:
    """Copies reviewable project text while excluding personal/runtime data."""

    def __init__(self, source_root: Path) -> None:
        self._source_root = source_root.resolve()

    def copy_to(self, destination: Path) -> BundleManifest:
        destination.mkdir(parents=True, exist_ok=True)
        selected: list[str] = []
        total_bytes = 0
        for source in sorted(self._source_root.rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(self._source_root)
            if not _is_allowed(relative):
                continue
            size = source.stat().st_size
            if size > _MAX_FILE_BYTES:
                continue
            total_bytes += size
            if total_bytes > _MAX_BUNDLE_BYTES or len(selected) >= _MAX_FILES:
                raise WorkerUnavailableError(
                    "Codex 작업 묶음이 안전 제한을 초과했습니다."
                )
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            selected.append(relative.as_posix())
        if not selected:
            raise WorkerUnavailableError("Codex에 전달할 안전한 프로젝트 파일이 없습니다.")
        return BundleManifest(tuple(selected), total_bytes)


def _is_allowed(relative: Path) -> bool:
    parts = relative.parts
    if not parts or any(part in _DENIED_PARTS or part.startswith(".") for part in parts):
        return False
    if relative.name.startswith(".env"):
        return False
    if len(parts) == 1:
        return (
            relative.name in _ROOT_FILES
            or relative.name.startswith(_ROOT_PREFIXES)
        ) and (relative.suffix in _ALLOWED_SUFFIXES or relative.name == "winter")
    return parts[0] in _ALLOWED_DIRECTORIES and relative.suffix in _ALLOWED_SUFFIXES


class CodexReadOnlyWorker:
    """Runs one non-persistent Codex task against a sanitized text bundle."""

    def __init__(
        self,
        source_root: Path,
        *,
        executable: str = "codex",
        model: str = "gpt-5.4",
        timeout_seconds: int = 600,
        runner: Callable[..., object] = subprocess.run,
    ) -> None:
        self._source_root = source_root.resolve()
        self._executable = executable
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def execute(self, request: ReadOnlyWorkerRequest) -> ReadOnlyWorkerResult:
        try:
            with TemporaryDirectory(prefix="winter-codex-worker-") as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                manifest = SafeProjectBundle(self._source_root).copy_to(workspace)
                schema_path = root / "result-schema.json"
                output_path = root / "result.json"
                schema_path.write_text(
                    json.dumps(_RESULT_SCHEMA, ensure_ascii=False), encoding="utf-8"
                )
                prompt = _worker_prompt(request, manifest)
                command = [
                    self._executable,
                    "exec",
                    "-m",
                    self._model,
                    "--disable",
                    "plugins",
                    "--disable",
                    "apps",
                    "--disable",
                    "multi_agent",
                    "--ephemeral",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "--color",
                    "never",
                    "-C",
                    str(workspace),
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(output_path),
                    "-",
                ]
                completed = self._runner(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=self._timeout_seconds,
                    cwd=workspace,
                    env=_minimal_environment(),
                    check=False,
                )
                if getattr(completed, "returncode", 1) != 0:
                    raise WorkerUnavailableError(
                        "Codex Worker를 사용할 수 없습니다. 인증과 CLI 상태를 확인하세요."
                    )
                return _parse_result(output_path, model=self._model)
        except WorkerUnavailableError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise WorkerUnavailableError(
                "Codex Worker를 사용할 수 없습니다. 실행 환경을 확인하세요."
            ) from error


def _worker_prompt(
    request: ReadOnlyWorkerRequest, manifest: BundleManifest
) -> str:
    return (
        "You are a read-only specialist Worker for Winter. "
        "Inspect only the sanitized files inside the current workspace. "
        "Never access paths outside it, network tools, credentials, environment "
        "variables, hidden files, personal memory, conversation databases, audio, "
        "or identity data. Do not modify files or run destructive commands. "
        "Answer the user's actual task in concise Korean. Preserve concrete file "
        "evidence as workspace-relative paths and state uncertainty honestly. "
        f"The bundle contains {len(manifest.relative_paths)} text files and "
        f"{manifest.total_bytes} bytes.\n\nUser task:\n{request.task}"
    )


def _parse_result(output_path: Path, *, model: str) -> ReadOnlyWorkerResult:
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        answer = str(payload["answer"]).strip()
        evidence = tuple(str(value).strip() for value in payload["evidence"])
        limitations = tuple(str(value).strip() for value in payload["limitations"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise WorkerUnavailableError(
            "Codex Worker가 검증 가능한 결과를 반환하지 않았습니다."
        ) from error
    try:
        return ReadOnlyWorkerResult(
            answer=answer,
            evidence=tuple(value for value in evidence if value),
            limitations=tuple(value for value in limitations if value),
            worker="codex",
            model=model,
        )
    except ValueError as error:
        raise WorkerUnavailableError(
            "Codex Worker가 비어 있는 결과를 반환했습니다."
        ) from error


def _minimal_environment() -> dict[str, str]:
    allowed = {
        "CODEX_HOME",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "SSL_CERT_FILE",
        "TERM",
        "USER",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}
