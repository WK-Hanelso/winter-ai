"""Reusable bubblewrap boundary for running untrusted repository verification."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import shutil
import subprocess


class VerificationSandboxError(RuntimeError):
    """Raised when verification cannot be launched inside the required sandbox."""


class BubblewrapVerificationSandbox:
    """Run fixed argv against a read-only workspace with no network or user home."""

    def __init__(
        self,
        *,
        sandbox_executable: str = "bwrap",
        timeout_seconds: int = 300,
        runner: Callable[..., object] = subprocess.run,
    ) -> None:
        self._sandbox_name = sandbox_executable
        self._sandbox_executable = shutil.which(sandbox_executable)
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    @property
    def available(self) -> bool:
        return self._sandbox_executable is not None or self._runner is not subprocess.run

    def run(self, workspace: Path, argv: tuple[str, ...]) -> object:
        root = workspace.resolve()
        if not root.is_dir():
            raise VerificationSandboxError("verification workspace is missing")
        if not argv or any(not value for value in argv):
            raise VerificationSandboxError("verification argv is invalid")
        sandbox = self._sandbox_executable or self._sandbox_name
        if not self.available:
            raise VerificationSandboxError(
                "bubblewrap verification sandbox is unavailable"
            )
        executable = self._resolve_executable(argv[0])
        runtime_bind, sandbox_executable = _sandbox_executable(executable)
        command = [
            sandbox,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind-try",
            "/lib64",
            "/lib64",
            "--ro-bind",
            "/etc",
            "/etc",
        ]
        if runtime_bind is not None:
            command.extend(("--ro-bind", str(runtime_bind), "/runtime"))
        command.extend(
            (
                "--ro-bind",
                str(root),
                "/workspace",
                "--tmpfs",
                "/tmp",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--chdir",
                "/workspace",
                "--setenv",
                "HOME",
                "/nonexistent",
                "--setenv",
                "PATH",
                "/runtime/bin:/usr/local/bin:/usr/bin:/bin",
                "--setenv",
                "PYTHONDONTWRITEBYTECODE",
                "1",
                "--",
                sandbox_executable,
                *argv[1:],
            )
        )
        try:
            return self._runner(
                command,
                text=True,
                capture_output=True,
                timeout=self._timeout_seconds,
                cwd=root,
                env={
                    "LANG": "C.UTF-8",
                    "PATH": os.defpath,
                    "PYTHONUTF8": "1",
                },
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise VerificationSandboxError(
                f"sandboxed verification launch failed: {type(error).__name__}"
            ) from error

    def _resolve_executable(self, value: str) -> Path:
        candidate = Path(value)
        resolved = (
            candidate.resolve()
            if candidate.is_absolute() and candidate.is_file()
            else Path(found).resolve()
            if (found := shutil.which(value)) is not None
            else None
        )
        if resolved is None or not resolved.is_file():
            if self._runner is not subprocess.run:
                return Path("/usr/bin") / candidate.name
            raise VerificationSandboxError(
                f"verification executable is unavailable: {value}"
            )
        return resolved


def _sandbox_executable(executable: Path) -> tuple[Path | None, str]:
    if _is_within(executable, Path("/usr")) or _is_within(executable, Path("/bin")):
        return None, str(executable)
    runtime = executable.parent.parent
    try:
        relative = executable.relative_to(runtime)
    except ValueError as error:
        raise VerificationSandboxError(
            "verification executable runtime cannot be isolated"
        ) from error
    return runtime, str(Path("/runtime") / relative)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
