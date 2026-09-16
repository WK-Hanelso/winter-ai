from pathlib import Path

import pytest

from companion.adapters.codex_worker import CodexReadOnlyWorker, SafeProjectBundle
from companion.worker import (
    ExplicitCodexRoutingPolicy,
    ReadOnlyWorkerRequest,
    ReadOnlyWorkerResult,
    WorkerUnavailableError,
)


def test_explicit_codex_route_requires_an_action_not_a_mention() -> None:
    policy = ExplicitCodexRoutingPolicy()

    assert policy.route("코덱스로 지금 코드 원인을 분석해줘") is not None
    assert policy.route("코드엑스한테 README를 검토해달라고 해줘") is not None
    assert policy.route("Codex를 써서 이 프로젝트를 찾아봐줘") is not None
    assert policy.route("코덱스가 뭐야?") is None
    assert policy.route("오늘 운동이 너무 힘들었어") is None


def test_safe_project_bundle_excludes_private_and_binary_material(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "bundle"
    (source / "src").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "data").mkdir()
    (source / "review").mkdir()
    (source / ".codex").mkdir()
    (source / "src" / "app.py").write_text("print('safe')\n")
    (source / "tests" / "test_app.py").write_text("def test_ok(): pass\n")
    (source / "README.md").write_text("safe project description\n")
    (source / ".env").write_text("SECRET=never-copy\n")
    (source / "data" / "memories.sqlite").write_bytes(b"private")
    (source / "review" / "voice.wav").write_bytes(b"private")
    (source / ".codex" / "auth.json").write_text("private")
    (source / "src" / "recording.wav").write_bytes(b"private")

    manifest = SafeProjectBundle(source).copy_to(destination)

    assert set(manifest.relative_paths) == {
        "README.md",
        "src/app.py",
        "tests/test_app.py",
    }
    assert (destination / "src" / "app.py").is_file()
    assert not (destination / ".env").exists()
    assert not (destination / "data").exists()
    assert not (destination / "review").exists()
    assert "never-copy" not in "".join(
        path.read_text()
        for path in destination.rglob("*")
        if path.is_file()
    )


def test_codex_worker_uses_ephemeral_read_only_process_and_parses_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("winter mission")
    captured: dict[str, object] = {}

    def run_process(*args: object, **kwargs: object) -> object:
        command = args[0]
        captured["command"] = command
        captured["input"] = kwargs["input"]
        output_path = Path(command[command.index("-o") + 1])  # type: ignore[index,union-attr]
        output_path.write_text(
            '{"answer":"분석 결과야.","evidence":["README.md:1"],'
            '"limitations":["읽기 전용"]}',
            encoding="utf-8",
        )

        class Completed:
            returncode = 0
            stderr = ""

        return Completed()

    worker = CodexReadOnlyWorker(source, executable="codex", runner=run_process)

    result = worker.execute(ReadOnlyWorkerRequest("코덱스로 README를 분석해줘"))

    command = captured["command"]
    assert "--ephemeral" in command  # type: ignore[operator]
    assert command[command.index("--sandbox") + 1] == "read-only"  # type: ignore[index,union-attr]
    assert "--disable" in command  # type: ignore[operator]
    assert "plugins" in command  # type: ignore[operator]
    assert result == ReadOnlyWorkerResult(
        answer="분석 결과야.",
        evidence=("README.md:1",),
        limitations=("읽기 전용",),
        worker="codex",
        model="gpt-5.4",
    )


def test_codex_worker_does_not_silently_fallback_on_failure(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("winter mission")

    def fail(*args: object, **kwargs: object) -> object:
        class Completed:
            returncode = 1
            stderr = "authentication failed: secret-detail"

        return Completed()

    worker = CodexReadOnlyWorker(source, runner=fail)

    with pytest.raises(WorkerUnavailableError, match="Codex Worker를 사용할 수 없습니다"):
        worker.execute(ReadOnlyWorkerRequest("코덱스로 분석해줘"))
