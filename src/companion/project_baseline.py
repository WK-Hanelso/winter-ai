"""Privacy-safe project manifest shared by isolated work and source adoption."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath

_ROOT_FILES = {
    "AGENTS.md",
    "README.md",
    "project-state.toml",
    "pyproject.toml",
    "winter",
}
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
MAX_SAFE_FILE_BYTES = 512_000
MAX_SAFE_BUNDLE_BYTES = 12_000_000
MAX_SAFE_FILES = 1_500


class ProjectBaselineError(RuntimeError):
    """Raised when a privacy-safe manifest cannot be built exactly."""


@dataclass(frozen=True)
class ProjectFileDigest:
    path: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        relative = PurePosixPath(self.path)
        if (
            relative.is_absolute()
            or relative.as_posix() != self.path
            or not is_safe_project_path(Path(self.path))
        ):
            raise ProjectBaselineError("project baseline contains an unsafe path")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ProjectBaselineError("project baseline contains an invalid hash")
        if self.size < 0 or self.size > MAX_SAFE_FILE_BYTES:
            raise ProjectBaselineError("project baseline contains an invalid size")


@dataclass(frozen=True)
class ProjectBaseline:
    files: tuple[ProjectFileDigest, ...]
    manifest_sha256: str
    total_bytes: int

    def __post_init__(self) -> None:
        paths = tuple(item.path for item in self.files)
        if not paths or paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ProjectBaselineError(
                "project baseline requires unique sorted safe paths"
            )
        if self.total_bytes != sum(item.size for item in self.files):
            raise ProjectBaselineError("project baseline byte count does not match")
        if (
            len(self.files) > MAX_SAFE_FILES
            or self.total_bytes > MAX_SAFE_BUNDLE_BYTES
        ):
            raise ProjectBaselineError("project baseline exceeds safe limits")
        expected = _manifest_hash(self.files)
        if self.manifest_sha256 != expected:
            raise ProjectBaselineError("project baseline manifest hash does not match")


def build_project_baseline(source_root: Path) -> ProjectBaseline:
    root = source_root.resolve()
    if not root.is_dir():
        raise ProjectBaselineError("project baseline root is not a directory")
    files: list[ProjectFileDigest] = []
    total_bytes = 0
    try:
        candidates = sorted(root.rglob("*"))
        for source in candidates:
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(root)
            if not is_safe_project_path(relative):
                continue
            size = source.stat().st_size
            if size > MAX_SAFE_FILE_BYTES:
                continue
            total_bytes += size
            if total_bytes > MAX_SAFE_BUNDLE_BYTES or len(files) >= MAX_SAFE_FILES:
                raise ProjectBaselineError("safe project baseline exceeds limits")
            content = source.read_bytes()
            if len(content) != size:
                raise ProjectBaselineError(
                    "safe project file changed while building the baseline"
                )
            files.append(
                ProjectFileDigest(
                    path=relative.as_posix(),
                    sha256=hashlib.sha256(content).hexdigest(),
                    size=size,
                )
            )
    except OSError as error:
        raise ProjectBaselineError("could not read safe project baseline") from error
    if not files:
        raise ProjectBaselineError("safe project baseline contains no files")
    entries = tuple(files)
    return ProjectBaseline(
        files=entries,
        manifest_sha256=_manifest_hash(entries),
        total_bytes=total_bytes,
    )


def is_safe_project_path(relative: Path) -> bool:
    parts = relative.parts
    if not parts or any(part in _DENIED_PARTS or part.startswith(".") for part in parts):
        return False
    if relative.name.startswith(".env"):
        return False
    if len(parts) == 1:
        if relative.name in _ROOT_FILES:
            return relative.suffix in _ALLOWED_SUFFIXES or relative.name == "winter"
        if relative.name.startswith("Dockerfile"):
            return True
        if relative.name.startswith("compose."):
            return relative.suffix in _ALLOWED_SUFFIXES
        return False
    return parts[0] in _ALLOWED_DIRECTORIES and relative.suffix in _ALLOWED_SUFFIXES


def _manifest_hash(files: tuple[ProjectFileDigest, ...]) -> str:
    payload = [
        {"path": item.path, "sha256": item.sha256, "size": item.size}
        for item in files
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
