"""Exercise the Bash launcher without Docker, LLMs, credentials or production data."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def launch(
    tmp_path: Path, *arguments: str, connection: str = "ready",
    action: str = "text", search_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    source = Path(__file__).resolve().parents[2] / "winter"
    launcher = tmp_path / "winter"
    launcher.write_text(source.read_text())
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "web-search.sh").write_text(
        'echo "$1" >> "search-calls"\nexit "${SEARCH_EXIT:-0}"\n')
    (scripts / "text-llm.sh").write_text('exit 0\n')
    stub = tmp_path / "docker"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['DOCKER_ARGS'], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    stub.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "ready = os.environ['TEST_CONNECTION'] == 'ready' or pathlib.Path('connected').exists()\n"
        "sys.exit(0 if ready else 7)\n"
    )
    curl.chmod(0o755)
    ssh = tmp_path / "ssh"
    ssh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path('ssh-args.json').write_text(json.dumps(sys.argv[1:]))\n"
        "if os.environ['TEST_CONNECTION'] == 'recover':\n"
        "    pathlib.Path('connected').touch()\n"
        "else:\n"
        "    sys.exit(1)\n"
    )
    ssh.chmod(0o755)
    capture = tmp_path / "args.jsonl"
    result = subprocess.run(
        ["bash", str(launcher), action, *arguments],
        env=dict(os.environ, PATH=str(tmp_path) + ":" + os.environ["PATH"],
                 DOCKER_ARGS=str(capture), TEST_CONNECTION=connection, SEARCH_EXIT=str(search_exit),
                 WINTER_LLM_URL="http://127.0.0.1:18080"),
        text=True, capture_output=True, check=False,
    )
    calls = (
        [json.loads(line) for line in capture.read_text().splitlines()] if capture.exists() else []
    )
    return result, calls


def test_text_launches_real_shared_core_with_only_explicit_mounts(tmp_path: Path) -> None:
    result, calls = launch(tmp_path, "--prompt", "hello")
    assert result.returncode == 0, result.stderr
    command = calls[-1]
    assert command[0] == "run"
    assert "companion.user_cli" in command
    assert command[command.index("--dialogue-policy") + 1] == "companion"
    assert "--read-only" in command
    assert command[command.index("--network") + 1] == "host"
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "-v"]
    assert mounts == [f"{tmp_path}/src:/workspace/src:ro",
                      f"{tmp_path}/configs:/workspace/configs:ro",
                      f"{tmp_path}/data:/workspace/data:rw"]
    assert not any(".env" in item or "docker.sock" in item or ".codex" in item for item in command)


@pytest.mark.parametrize("option", ["--codex-worker", "--winter-work-canary", "--backend=fake"])
def test_text_rejects_worker_and_fake_options_before_starting_docker(
    tmp_path: Path, option: str,
) -> None:
    result, calls = launch(tmp_path, option)
    assert result.returncode == 2
    assert not calls


def test_cli_does_not_allow_abbreviated_worker_or_backend_options() -> None:
    from companion.user_cli import build_parser

    for options in (["--codex-w"], ["--back", "fake"]):
        with pytest.raises(SystemExit):
            build_parser().parse_args(options)


def test_text_does_not_recover_orin_tunnel_automatically(tmp_path: Path) -> None:
    result, calls = launch(tmp_path, "--prompt", "hello", connection="recover")
    assert result.returncode == 1
    assert not (tmp_path / "ssh-args.json").exists()
    assert all(call[0] != "run" for call in calls)


def test_tunnel_failure_does_not_start_core_or_fake_backend(tmp_path: Path) -> None:
    result, calls = launch(tmp_path, connection="failed")
    assert result.returncode == 1
    assert all(call[0] != "run" for call in calls)


def test_text_starts_search_server(tmp_path: Path) -> None:
    result, calls = launch(tmp_path, "--prompt", "hello")
    assert result.returncode == 0 and calls[-1][0] == "run"
    assert (tmp_path / "search-calls").read_text().splitlines() == ["start"]


def test_search_start_failure_does_not_stop_dialogue(tmp_path: Path) -> None:
    result, calls = launch(tmp_path, "--prompt", "hello", search_exit=1)
    assert result.returncode == 0 and calls[-1][0] == "run"
    assert "검색 서버 없이 시작합니다." in result.stdout


def test_stop_also_stops_search_server(tmp_path: Path) -> None:
    result, calls = launch(tmp_path, action="stop")
    assert result.returncode == 0
    assert (tmp_path / "search-calls").read_text().splitlines() == ["stop"]
    assert calls[-1][-2:] == ["stop", "llm"]
