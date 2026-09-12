"""Exercise pytest isolation in synthetic repositories, including collection writes."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


def run_session(root: Path, body: str) -> subprocess.CompletedProcess[str]:
    tests = root / "tests"
    tests.mkdir()
    conftest = Path(__file__).resolve().parents[1] / "conftest.py"
    (tests / "conftest.py").write_text(conftest.read_text())
    (tests / "test_probe.py").write_text(body)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True, timeout=30,
        env=dict(os.environ, WINTER_DATA_DIR=str(root / "data"),
                 PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS=""),
    )


def test_defaults_are_redirected_before_collection(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    result = run_session(tmp_path, """
import os
from pathlib import Path
from companion import user_cli, voice_cli, web
selected = Path(os.environ['WINTER_DATA_DIR'])
assert selected != Path.cwd() / 'data'
assert selected.is_absolute() and selected.is_dir()
assert user_cli.DATA == voice_cli.DEFAULT_DATA == web.DEFAULT_DATA == selected
def test_defaults():
    (selected / 'identity.json').write_text('synthetic')
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((tmp_path / "data").iterdir()) == []


@pytest.mark.parametrize("mutation", [
    "Path('data/existing.sqlite').write_bytes(b'changed')",
    "Path('data/identity.json').unlink()",
    "Path('data/new.sqlite').write_bytes(b'new')",
    "Path('data/existing.sqlite-wal').write_bytes(b'wal change')",
])
def test_collection_writes_fail_the_session(tmp_path: Path, mutation: str) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "existing.sqlite").write_bytes(b"original")
    (data / "identity.json").write_text("original")
    result = run_session(tmp_path, f"from pathlib import Path\n{mutation}\n"
                         "def test_probe():\n    pass\n")
    assert result.returncode != 0
    assert "Tests changed repository persistent state" in result.stdout


@pytest.mark.parametrize("state", ["absent", "read-only"])
def test_missing_or_readonly_data_is_reported(tmp_path: Path, state: str) -> None:
    data = tmp_path / "data"
    if state == "read-only":
        data.mkdir(mode=0o555)
    try:
        result = run_session(tmp_path, "def test_probe():\n    pass\n")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Winter store hash guard SKIPPED" in result.stdout
        assert state in result.stdout
    finally:
        if data.exists():
            data.chmod(0o755)
