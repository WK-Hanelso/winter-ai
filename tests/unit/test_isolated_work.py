import json
import os
from pathlib import Path
from typing import Any

import pytest

from companion.adapters.codex_isolated_worker import CodexIsolatedWorkWorker
from companion.isolated_work import (
    ImplementationAuthorization,
    IsolatedWorkError,
)
from companion.worker import WorkerUnavailableError


def _authorization(
    *allowed_paths: str,
    verification_profile: str = "python_unittest",
) -> ImplementationAuthorization:
    return ImplementationAuthorization(
        authorization_id="auth-001",
        status="approved",
        objective="덧셈 함수의 실제 오류를 수정한다.",
        allowed_paths=allowed_paths or ("src/calc.py",),
        verification_profile=verification_profile,
    )


def _source_project(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "src" / "calc.py").write_text(
        "def add(left: int, right: int) -> int:\n"
        "    return left - right\n",
        encoding="utf-8",
    )
    (source / "tests" / "test_calc.py").write_text(
        "import unittest\n\n"
        "from src.calc import add\n\n\n"
        "class CalcTest(unittest.TestCase):\n"
        "    def test_adds(self) -> None:\n"
        "        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )
    return source


def _write_worker_report(command: list[str], summary: str = "오류를 수정했다.") -> None:
    output_path = Path(command[command.index("-o") + 1])
    output_path.write_text(
        json.dumps({"summary": summary, "limitations": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def _config(command: list[str], key: str) -> object:
    prefix = f"{key}="
    for index, value in enumerate(command):
        if value == "-c" and command[index + 1].startswith(prefix):
            return json.loads(command[index + 1][len(prefix) :])
    raise AssertionError(f"missing config: {key}")


def _run_verification(*args: object, **kwargs: object) -> object:
    workspace = Path(kwargs["cwd"])  # type: ignore[arg-type]
    source = (workspace / "src" / "calc.py").read_text(encoding="utf-8")
    passed = "return 0" not in source

    class Completed:
        returncode = 0 if passed else 1
        stdout = "OK\n" if passed else "FAILED\n"
        stderr = ""

    return Completed()


@pytest.mark.parametrize(
    "status,path",
    [
        ("pending", "src/calc.py"),
        ("approved", "../outside.py"),
        ("approved", "/tmp/outside.py"),
        ("approved", ".env"),
        ("approved", "src/.secret.py"),
        ("approved", "src/payload.bin"),
    ],
)
def test_implementation_authorization_rejects_unapproved_or_unsafe_scope(
    status: str, path: str
) -> None:
    with pytest.raises(IsolatedWorkError):
        ImplementationAuthorization(
            authorization_id="auth-unsafe",
            status=status,
            objective="수정한다.",
            allowed_paths=(path,),
            verification_profile="python_unittest",
        )


def test_worker_modifies_only_copy_and_returns_verified_adoption_proposal(
    tmp_path: Path,
) -> None:
    source = _source_project(tmp_path)
    original = (source / "src" / "calc.py").read_bytes()
    codex_home = tmp_path / "real-codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"private":"credential"}')
    captured: dict[str, Any] = {}

    def verify_process(*args: object, **kwargs: object) -> object:
        captured["verification_command"] = list(args[0])  # type: ignore[arg-type]
        return _run_verification(*args, **kwargs)

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        captured["command"] = command
        captured["env"] = kwargs["env"]
        workspace = Path(command[command.index("-C") + 1])
        (workspace / "src" / "calc.py").write_text(
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n",
            encoding="utf-8",
        )
        isolated_home = Path(kwargs["env"]["CODEX_HOME"])  # type: ignore[index]
        assert isolated_home != codex_home
        assert (isolated_home / "auth.json").read_text() == '{"private":"credential"}'
        _write_worker_report(command)

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    result = CodexIsolatedWorkWorker(
        source,
        codex_home=codex_home,
        runner=run_process,
        verification_runner=verify_process,
    ).execute(_authorization("src/calc.py"))

    assert result.status == "awaiting_adoption"
    assert result.summary == "오류를 수정했다."
    assert result.changed_files[0].path == "src/calc.py"
    assert result.changed_files[0].change_type == "modified"
    assert result.changed_files[0].before_sha256 != result.changed_files[0].after_sha256
    assert "-    return left - right" in result.patch
    assert "+    return left + right" in result.patch
    assert result.verifications[0].name == "python_unittest"
    assert result.verifications[0].status == "pass"
    assert result.verifications[1].name == "python_structure"
    assert result.verifications[1].status == "pass"
    assert (source / "src" / "calc.py").read_bytes() == original

    command = captured["command"]
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    disabled = {
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--disable"
    }
    assert {"plugins", "apps", "multi_agent"} <= disabled
    assert "unified_exec" not in disabled
    assert "shell_tool" not in disabled
    assert _config(command, "approval_policy") == "never"
    assert _config(command, "web_search") == "disabled"
    assert _config(command, "sandbox_workspace_write.network_access") is False
    assert _config(command, "shell_environment_policy.inherit") == "core"
    assert _config(command, "shell_environment_policy.include_only") == [
        "PATH",
        "LANG",
        "LC_ALL",
        "PYTHONUTF8",
    ]
    assert _config(command, "shell_environment_policy.set.LANG") == "C.UTF-8"
    assert _config(command, "shell_environment_policy.set.PYTHONUTF8") == "1"
    rendered = json.dumps(command)
    assert "shell_environment_policy.set.CODEX_HOME" not in rendered
    assert "shell_environment_policy.set.HOME" not in rendered
    parent_env = captured["env"]
    assert parent_env["PATH"].endswith(os.defpath)
    assert parent_env["HOME"] != str(Path.home())
    verification_command = captured["verification_command"]
    assert "--unshare-all" in verification_command
    workspace_index = verification_command.index("/workspace")
    assert verification_command[workspace_index - 2] == "--ro-bind"
    assert str(source) not in verification_command
    assert "/workspace" in verification_command


def test_worker_rejects_change_outside_authorized_paths(tmp_path: Path) -> None:
    source = _source_project(tmp_path)
    original_test = (source / "tests" / "test_calc.py").read_bytes()

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        workspace = Path(command[command.index("-C") + 1])
        (workspace / "tests" / "test_calc.py").write_text(
            "# worker changed an unauthorized file\n",
            encoding="utf-8",
        )
        _write_worker_report(command)

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    worker = CodexIsolatedWorkWorker(source, runner=run_process)

    with pytest.raises(WorkerUnavailableError, match="허용 범위"):
        worker.execute(_authorization("src/calc.py"))
    assert (source / "tests" / "test_calc.py").read_bytes() == original_test


def test_worker_rejects_deleted_file(tmp_path: Path) -> None:
    source = _source_project(tmp_path)

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        workspace = Path(command[command.index("-C") + 1])
        (workspace / "src" / "calc.py").unlink()
        _write_worker_report(command)

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    with pytest.raises(WorkerUnavailableError, match="삭제"):
        CodexIsolatedWorkWorker(source, runner=run_process).execute(
            _authorization("src/calc.py")
        )


def test_worker_rejects_unexpected_hidden_file(tmp_path: Path) -> None:
    source = _source_project(tmp_path)

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        workspace = Path(command[command.index("-C") + 1])
        (workspace / "src" / "calc.py").write_text(
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n",
            encoding="utf-8",
        )
        (workspace / ".unexpected").write_text("hidden")
        _write_worker_report(command)

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    with pytest.raises(WorkerUnavailableError, match=r"\.unexpected"):
        CodexIsolatedWorkWorker(source, runner=run_process).execute(
            _authorization("src/calc.py")
        )


def test_failed_independent_verification_is_not_adoption_ready(tmp_path: Path) -> None:
    source = _source_project(tmp_path)

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        workspace = Path(command[command.index("-C") + 1])
        (workspace / "src" / "calc.py").write_text(
            "def add(left: int, right: int) -> int:\n"
            "    return 0\n",
            encoding="utf-8",
        )
        _write_worker_report(command, "수정했다고 판단했다.")

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    result = CodexIsolatedWorkWorker(
        source,
        runner=run_process,
        verification_runner=_run_verification,
    ).execute(
        _authorization("src/calc.py")
    )

    assert result.status == "verification_failed"
    assert result.verifications[0].status == "fail"
    assert "FAILED" in result.verifications[0].output.upper()


def test_duplicate_python_definition_fails_structure_gate(tmp_path: Path) -> None:
    source = _source_project(tmp_path)

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        workspace = Path(command[command.index("-C") + 1])
        (workspace / "src" / "calc.py").write_text(
            "def add(left: int, right: int) -> int:\n"
            "    return left - right\n"
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n",
            encoding="utf-8",
        )
        _write_worker_report(command)

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    result = CodexIsolatedWorkWorker(
        source,
        runner=run_process,
        verification_runner=_run_verification,
    ).execute(
        _authorization("src/calc.py")
    )

    assert result.status == "verification_failed"
    assert result.verifications[0].status == "pass"
    assert result.verifications[1].status == "fail"
    assert "duplicate top-level definitions" in result.verifications[1].output


def test_worker_rejects_unknown_verification_profile() -> None:
    with pytest.raises(IsolatedWorkError, match="검증 profile"):
        _authorization("src/calc.py", verification_profile="model_command")


def test_worker_rejects_change_over_approved_line_budget(tmp_path: Path) -> None:
    source = _source_project(tmp_path)

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        workspace = Path(command[command.index("-C") + 1])
        (workspace / "src" / "calc.py").write_text(
            "def add(left: int, right: int) -> int:\n"
            "    value = left + right\n"
            "    value = int(value)\n"
            "    return value\n",
            encoding="utf-8",
        )
        _write_worker_report(command)

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    authorization = ImplementationAuthorization(
        authorization_id="auth-budget",
        status="approved",
        objective="최소 수정한다.",
        allowed_paths=("src/calc.py",),
        verification_profile="python_unittest",
        max_changed_lines=2,
    )
    with pytest.raises(WorkerUnavailableError, match="line 예산"):
        CodexIsolatedWorkWorker(source, runner=run_process).execute(authorization)


def test_worker_rejects_empty_change(tmp_path: Path) -> None:
    source = _source_project(tmp_path)

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        _write_worker_report(command, "바꿀 필요가 없다.")

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    with pytest.raises(WorkerUnavailableError, match="변경을 만들지"):
        CodexIsolatedWorkWorker(source, runner=run_process).execute(
            _authorization("src/calc.py")
        )


def test_worker_fails_closed_without_independent_verification_sandbox(
    tmp_path: Path,
) -> None:
    source = _source_project(tmp_path)

    def run_process(*args: object, **kwargs: object) -> object:
        command = list(args[0])  # type: ignore[arg-type]
        workspace = Path(command[command.index("-C") + 1])
        (workspace / "src" / "calc.py").write_text(
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n",
            encoding="utf-8",
        )
        _write_worker_report(command)

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    worker = CodexIsolatedWorkWorker(
        source,
        runner=run_process,
        verification_sandbox_executable="/missing/winter-bwrap",
    )

    with pytest.raises(WorkerUnavailableError, match="검증 sandbox"):
        worker.execute(_authorization("src/calc.py"))
