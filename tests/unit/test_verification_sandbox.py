from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from companion.verification_sandbox import (
    BubblewrapVerificationSandbox,
    VerificationSandboxError,
)


def test_sandbox_uses_read_only_workspace_minimal_env_and_fixed_argv(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> object:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="OK", stderr="")

    result = BubblewrapVerificationSandbox(
        sandbox_executable="winter-bwrap",
        runner=runner,
    ).run(workspace, ("python", "-m", "unittest", "discover"))

    assert result.returncode == 0  # type: ignore[attr-defined]
    command, kwargs = calls[0]
    workspace_index = command.index(str(workspace.resolve()))
    assert command[workspace_index - 1] == "--ro-bind"
    assert command[workspace_index + 1] == "/workspace"
    assert "--unshare-all" in command
    assert "--die-with-parent" in command
    assert "--new-session" in command
    assert command[command.index("--") + 2 :] == [
        "-m",
        "unittest",
        "discover",
    ]
    assert kwargs["env"] == {
        "LANG": "C.UTF-8",
        "PATH": "/bin:/usr/bin",
        "PYTHONUTF8": "1",
    }
    assert "shell" not in kwargs


def test_sandbox_fails_closed_when_bubblewrap_is_missing(tmp_path: Path) -> None:
    with pytest.raises(VerificationSandboxError, match="unavailable"):
        BubblewrapVerificationSandbox(
            sandbox_executable="/missing/winter-bwrap",
        ).run(tmp_path, ("python", "-V"))


def test_sandbox_timeout_is_failure_and_never_retries_on_host(tmp_path: Path) -> None:
    calls = 0

    def runner(command: list[str], **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(command, timeout=1)

    with pytest.raises(VerificationSandboxError, match="TimeoutExpired"):
        BubblewrapVerificationSandbox(
            sandbox_executable="winter-bwrap",
            timeout_seconds=1,
            runner=runner,
        ).run(tmp_path, ("python", "-c", "pass"))

    assert calls == 1
