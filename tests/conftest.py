"""Keep pytest defaults away from personal state before test-module imports."""

from collections.abc import Iterator
import hashlib
import os
from pathlib import Path
import stat
import tempfile

import pytest

# Fixtures run too late for modules that freeze DEFAULT_DATA at import time.
# Do not honor an inherited production WINTER_DATA_DIR, TMPDIR or --basetemp.
_PREVIOUS_DATA_DIR = os.environ.get("WINTER_DATA_DIR")
_TEST_DATA = tempfile.TemporaryDirectory(prefix="winter-pytest-data-", dir="/tmp")
os.environ["WINTER_DATA_DIR"] = _TEST_DATA.name
_REPOSITORY_DATA = Path(__file__).resolve().parents[1] / "data"
_BEFORE: dict[str, str] | None = None
_NOTICE = ""


def _hashes(directory: Path) -> dict[str, str]:
    paths = set(directory.glob("*.sqlite"))
    # Include WAL sidecars: a committed write need not change the main DB yet.
    paths.update(directory.glob("*.sqlite-wal"))
    identity = directory / "identity.json"
    if identity.exists():
        paths.add(identity)
    result = {}
    for path in sorted(paths):
        with path.open("rb") as handle:
            result[path.name] = hashlib.file_digest(handle, "sha256").hexdigest()
    return result


def pytest_configure(config: pytest.Config) -> None:
    """Record before collection as well as before any test fixture writes."""
    global _BEFORE, _NOTICE
    if not _REPOSITORY_DATA.exists():
        _NOTICE = "Winter store hash guard SKIPPED: repository data/ is absent."
        return
    readonly = (
        not os.access(_REPOSITORY_DATA, os.W_OK)
        or not (_REPOSITORY_DATA.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        or bool(os.statvfs(_REPOSITORY_DATA).f_flag & os.ST_RDONLY)
    )
    if readonly:
        _NOTICE = "Winter store hash guard SKIPPED: repository data/ is read-only."
        return
    # An unreadable file in writable data/ is an error, not a silent skipped guard.
    _BEFORE = _hashes(_REPOSITORY_DATA)
    _NOTICE = f"Winter store hash guard: recorded {len(_BEFORE)} repository files."


@pytest.fixture(scope="session", autouse=True)
def protect_repository_stores() -> Iterator[None]:
    """Fail the session on changed, removed or newly created persistent files."""
    yield
    if _BEFORE is None:
        return
    after = _hashes(_REPOSITORY_DATA)
    changed = sorted(name for name in _BEFORE.keys() | after.keys()
                     if _BEFORE.get(name) != after.get(name))
    if changed:
        pytest.fail("Tests changed repository persistent state: " + ", ".join(changed))


def pytest_terminal_summary(terminalreporter: object) -> None:
    # Visible even with pytest -q and successful-test output capture.
    terminalreporter.write_line(_NOTICE)  # type: ignore[attr-defined]
    terminalreporter.write_line(  # type: ignore[attr-defined]
        f"Winter test defaults isolated at: {_TEST_DATA.name}"
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    if _PREVIOUS_DATA_DIR is None:
        os.environ.pop("WINTER_DATA_DIR", None)
    else:
        os.environ["WINTER_DATA_DIR"] = _PREVIOUS_DATA_DIR
    _TEST_DATA.cleanup()
