"""Offline launcher contracts; no real Docker, GPU, SSH or personal data."""

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Protocol

import pytest


class Launcher(Protocol):
    def __call__(self, *args: str, **updates: str) -> subprocess.CompletedProcess[str]: ...


@pytest.fixture
def runtime(tmp_path: Path) -> Launcher:
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "scripts").mkdir()
    for relative in ("winter", "scripts/text-llm.sh"):
        (tmp_path / relative).write_text((root / relative).read_text())
    # Search lifecycle is covered separately; never start the real search container.
    (tmp_path / "scripts/web-search.sh").write_text("exit 0\n")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"synthetic")
    (tmp_path / "docker").write_text(
        f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
a = sys.argv[1:]
with open('calls.jsonl', 'a') as out:
    out.write(json.dumps(a) + '\n')
p = pathlib.Path('server.json')
s = json.loads(p.read_text()) if p.exists() else None
if a == ['info'] and os.environ.get('TEST_DOCKER_DOWN'):
    sys.exit(1)
elif a[:2] == ['container', 'inspect']:
    if not s:
        sys.exit(1)
    if '--format' in a:
        fmt = a[a.index('--format') + 1]
        print(('true' if s['running'] else 'false') if 'State.Running' in fmt else s['owner'])
elif a[:2] == ['image', 'inspect']:
    if os.environ.get('TEST_MISSING_IMAGE'):
        sys.exit(1)
    print('sha256:test-image')
elif a[:1] == ['run'] and '-d' in a:
    s = {'running': not bool(os.environ.get('TEST_CRASH')), 'owner':
         'rtx2060-hybrid-v1|' + str(pathlib.Path.cwd())}
    p.write_text(json.dumps(s))
elif a[:1] == ['stop']:
    s['running'] = False
    p.write_text(json.dumps(s))
elif a[:1] == ['rm']:
    p.unlink()
'''
    )
    (tmp_path / "curl").write_text(
        f"#!{sys.executable}\n" +
        "import json, os, pathlib, sys\n"
        "p = pathlib.Path('server.json')\n"
        "ready = (p.exists() and json.loads(p.read_text())['running'])\n"
        "sys.exit(0 if ready or os.environ.get('TEST_BUSY_PORT') else 7)\n"
    )
    (tmp_path / "ssh").write_text("#!/bin/sh\nexit 99\n")
    for name in ("docker", "curl", "ssh"):
        (tmp_path / name).chmod(0o755)
    environment = dict(os.environ, PATH=str(tmp_path) + ":" + os.environ["PATH"],
                       WINTER_TEXT_MODEL_PATH=str(model))
    environment.pop("WINTER_LLM_URL", None)
    environment.pop("WINTER_TEXT_DATA_DIR", None)

    def run(*args: str, **updates: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(tmp_path / "winter"), *args],
                              cwd=tmp_path, env=dict(environment, **updates),
                              text=True, capture_output=True, timeout=15, check=False)

    return run


def calls(tmp_path: Path) -> list[list[str]]:
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]


def test_default_text_starts_host_profile_reuses_then_stops_and_restarts(
    runtime: Launcher, tmp_path: Path,
) -> None:
    for _ in range(2):
        result = runtime("text", "--prompt", "hello")
        assert result.returncode == 0, result.stderr
    commands = calls(tmp_path)
    servers = [cmd for cmd in commands if cmd[:2] == ["run", "-d"]]
    assert len(servers) == 1
    server = servers[0]
    assert "GGML_CUDA_DISABLE_GRAPHS=1" in server
    assert server[server.index("--n-cpu-moe") + 1] == "44"
    assert server[server.index("--memory-swap") + 1] == "20g"
    assert server[server.index("--port") + 1] == "18081"
    assert "sha256:test-image" in server
    assert not any("identity" in arg or "sqlite" in arg for arg in server)
    assert "http://127.0.0.1:18081" in commands[-1]
    assert runtime("status").returncode == 0
    assert runtime("stop").returncode == 0
    assert not (tmp_path / "server.json").exists()
    assert runtime("stop").returncode == 0
    assert runtime("text", "--prompt", "again").returncode == 0
    assert sum(cmd[:2] == ["run", "-d"] for cmd in calls(tmp_path)) == 2
    assert ["stop", "--time", "10", "winter-text-llm"] in calls(tmp_path)


@pytest.mark.parametrize("command", [("text", "--help"), ("text", "--show-history"),
                                    ("status",), ("stop",)])
def test_no_model_for_help_history_status_or_stop(
    runtime: Launcher, tmp_path: Path, command: tuple[str, ...],
) -> None:
    assert runtime(*command).returncode == 0
    assert not (tmp_path / "server.json").exists()
    assert not any(cmd[:2] == ["run", "-d"] for cmd in calls(tmp_path))


@pytest.mark.parametrize("option", ["TEST_CRASH", "TEST_MISSING_IMAGE", "TEST_BUSY_PORT"])
def test_model_failure_never_starts_core(
    runtime: Launcher, tmp_path: Path, option: str,
) -> None:
    result = runtime("text", "--prompt", "hello", **{option: "1"})
    assert result.returncode == 1
    assert not any("companion.user_cli" in cmd for cmd in calls(tmp_path))


def test_missing_model_does_not_create_container(runtime: Launcher, tmp_path: Path) -> None:
    result = runtime("text", "--prompt", "hello", WINTER_TEXT_MODEL_PATH="missing.gguf")
    assert result.returncode == 1
    assert not any(cmd[:1] == ["run"] for cmd in calls(tmp_path))


@pytest.mark.parametrize("command", [("stop",), ("text", "--prompt", "hello")])
def test_foreign_named_container_is_not_modified(
    runtime: Launcher, tmp_path: Path, command: tuple[str, ...],
) -> None:
    (tmp_path / "server.json").write_text(json.dumps({"running": True, "owner": "foreign"}))
    assert runtime(*command).returncode == 1
    assert not any(cmd[0] in {"run", "stop", "rm"} for cmd in calls(tmp_path))


def test_isolated_data_directory_is_used_for_cli_mount(runtime: Launcher, tmp_path: Path) -> None:
    result = runtime("text", "--prompt", "hello", WINTER_TEXT_DATA_DIR="isolated")
    assert result.returncode == 0, result.stderr
    assert f"{tmp_path}/isolated:/workspace/data:rw" in calls(tmp_path)[-1]


@pytest.mark.parametrize("command", [("status",), ("stop",), ("text", "--prompt", "hello")])
def test_daemon_failure_is_not_reported_as_stopped(
    runtime: Launcher, tmp_path: Path, command: tuple[str, ...],
) -> None:
    result = runtime(*command, TEST_DOCKER_DOWN="1")
    assert result.returncode == 1
    assert "Docker에 연결할 수 없어" in result.stderr
    assert not any(cmd[0] in {"run", "stop", "rm"} for cmd in calls(tmp_path))


def test_context_size_environment_only_changes_server_context(
    runtime: Launcher, tmp_path: Path,
) -> None:
    assert runtime("text", "--prompt", "hello", WINTER_TEXT_CTX_SIZE="").returncode == 0
    server = next(cmd for cmd in calls(tmp_path) if cmd[:2] == ["run", "-d"])
    # Default raised to 8192 after P2 scene runs overflowed 4096 with the schedule block.
    assert server[server.index("--ctx-size") + 1] == "8192"
    assert runtime("stop").returncode == 0
    assert runtime("text", "--prompt", "hello", WINTER_TEXT_CTX_SIZE="4096").returncode == 0
    server = [cmd for cmd in calls(tmp_path) if cmd[:2] == ["run", "-d"]][-1]
    assert server[server.index("--ctx-size") + 1] == "4096"
    assert server[server.index("--parallel") + 1] == "1"
