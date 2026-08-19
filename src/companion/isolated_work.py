"""Contracts for an approved implementation that must stop before adoption."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from companion.project_baseline import ProjectBaseline

_VERIFICATION_PROFILES = frozenset({"python_unittest"})
_EDITABLE_SUFFIXES = frozenset(
    {
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
)


class IsolatedWorkError(ValueError):
    """Raised when an implementation authorization or result is unsafe."""


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise IsolatedWorkError(f"{field_name} 값이 비어 있습니다.")
    return normalized


def _safe_relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or any(part.startswith(".") for part in path.parts)
    ):
        raise IsolatedWorkError("허용 경로는 숨김 요소가 없는 상대 경로여야 합니다.")
    if (
        path.name != "winter"
        and not path.name.startswith("Dockerfile")
        and path.suffix.lower() not in _EDITABLE_SUFFIXES
    ):
        raise IsolatedWorkError("허용 경로는 검토 가능한 text source여야 합니다.")
    return path.as_posix()


@dataclass(frozen=True)
class ImplementationAuthorization:
    """A user's approval to implement, distinct from approval to adopt."""

    authorization_id: str
    status: str
    objective: str
    allowed_paths: tuple[str, ...]
    verification_profile: str
    max_changed_lines: int = 200

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "authorization_id", _required(self.authorization_id, "authorization_id")
        )
        object.__setattr__(self, "objective", _required(self.objective, "objective"))
        if self.status != "approved":
            raise IsolatedWorkError("승인된 구현 요청만 격리 Worker에 전달할 수 있습니다.")
        normalized_paths = tuple(
            dict.fromkeys(_safe_relative_path(value) for value in self.allowed_paths)
        )
        if not normalized_paths:
            raise IsolatedWorkError("수정 허용 경로가 비어 있습니다.")
        object.__setattr__(self, "allowed_paths", normalized_paths)
        if self.verification_profile not in _VERIFICATION_PROFILES:
            raise IsolatedWorkError("등록되지 않은 검증 profile입니다.")
        if not 1 <= self.max_changed_lines <= 5_000:
            raise IsolatedWorkError("변경 line 예산은 1에서 5000 사이여야 합니다.")


@dataclass(frozen=True)
class ChangedFile:
    path: str
    change_type: str
    before_sha256: str | None
    after_sha256: str


@dataclass(frozen=True)
class VerificationResult:
    name: str
    status: str
    exit_code: int
    output: str


@dataclass(frozen=True)
class IsolatedWorkResult:
    """A verified patch proposal. It is not permission to mutate the source."""

    proposal_id: str
    authorization_id: str
    status: str
    summary: str
    changed_files: tuple[ChangedFile, ...]
    patch: str
    verifications: tuple[VerificationResult, ...]
    limitations: tuple[str, ...]
    worker: str
    model: str
    project_baseline: ProjectBaseline | None = None

    def __post_init__(self) -> None:
        if self.status not in {"awaiting_adoption", "verification_failed"}:
            raise IsolatedWorkError("알 수 없는 격리 작업 상태입니다.")
        if not self.changed_files or not self.patch:
            raise IsolatedWorkError("격리 작업 결과에는 실제 변경이 있어야 합니다.")
        if not self.verifications:
            raise IsolatedWorkError("격리 작업 결과에는 독립 검증이 있어야 합니다.")
