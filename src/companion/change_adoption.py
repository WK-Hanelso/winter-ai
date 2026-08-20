"""Fail-closed application and rollback of approved isolated change proposals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Protocol

from companion.change_proposal import (
    ChangeFileSnapshot,
    ChangeProposalError,
    SqliteChangeProposalRepository,
    StoredChangeProposal,
)
from companion.isolated_work import VerificationResult
from companion.project_baseline import (
    ProjectBaselineError,
    build_project_baseline,
    is_safe_project_path,
)
from companion.verification_sandbox import BubblewrapVerificationSandbox

_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)\n?$"
)


class ChangeAdoptionError(RuntimeError):
    """Raised when an approved patch cannot be applied or rolled back safely."""


class _StaleSourceError(ChangeAdoptionError):
    pass


class RepositoryVerifier(Protocol):
    def verify(self, source_root: Path) -> tuple[VerificationResult, ...]: ...


@dataclass(frozen=True)
class VerificationCommand:
    name: str
    command: tuple[str, ...]


class CommandRepositoryVerifier:
    """Run a fixed full-repository profile without a shell or Worker control."""

    def __init__(
        self,
        commands: tuple[VerificationCommand, ...],
        *,
        timeout_seconds: int = 300,
        sandbox_executable: str = "bwrap",
        runner: Callable[..., object] = subprocess.run,
    ) -> None:
        if not commands or any(
            not item.name.strip() or not item.command or any(not part for part in item.command)
            for item in commands
        ):
            raise ChangeAdoptionError(
                "full repository verification command가 올바르지 않습니다."
            )
        self._commands = commands
        self._sandbox = BubblewrapVerificationSandbox(
            sandbox_executable=sandbox_executable,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )

    def verify(self, source_root: Path) -> tuple[VerificationResult, ...]:
        results: list[VerificationResult] = []
        for item in self._commands:
            completed = self._sandbox.run(source_root, item.command)
            exit_code = int(getattr(completed, "returncode", 1))
            stdout = str(getattr(completed, "stdout", ""))
            stderr = str(getattr(completed, "stderr", ""))
            output = (stdout + ("\n" if stdout and stderr else "") + stderr)[-20_000:]
            results.append(
                VerificationResult(
                    name=item.name,
                    status="pass" if exit_code == 0 else "fail",
                    exit_code=exit_code,
                    output=output,
                )
            )
            if exit_code != 0:
                break
        return tuple(results)


@dataclass(frozen=True)
class ChangeAdoptionResult:
    proposal_id: str
    status: str
    changed_paths: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class _PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _FilePatch:
    path: str
    hunks: tuple[_PatchHunk, ...]


@dataclass(frozen=True)
class _PreparedChange:
    path: str
    target: Path
    before: bytes | None
    after: bytes


class ChangeAdoptionService:
    """Apply exact approved bytes and retain enough local evidence to undo them."""

    def __init__(
        self,
        source_root: Path,
        proposals: SqliteChangeProposalRepository,
        *,
        replace_file: Callable[[Path, Path], None] = os.replace,
        repository_verifier: RepositoryVerifier | None = None,
    ) -> None:
        try:
            resolved = source_root.resolve(strict=True)
        except OSError as error:
            raise ChangeAdoptionError("적용할 source root를 찾지 못했습니다.") from error
        if not resolved.is_dir():
            raise ChangeAdoptionError("적용할 source root가 directory가 아닙니다.")
        self._source_root = resolved
        self._proposals = proposals
        self._replace_file = replace_file
        self._repository_verifier = repository_verifier

    def apply(
        self,
        proposal_id: str,
        *,
        expected_patch_sha256: str,
    ) -> ChangeAdoptionResult:
        proposal = self._require_proposal(proposal_id, "adoption_approved")
        if proposal.patch_sha256 != expected_patch_sha256:
            raise ChangeAdoptionError(
                "천우가 확인한 patch hash와 적용할 수정안 hash가 다릅니다."
            )
        if proposal.result.project_baseline is None:
            raise ChangeAdoptionError(
                "project baseline이 없는 이전 수정안은 적용할 수 없습니다."
            )
        if self._repository_verifier is None:
            raise ChangeAdoptionError(
                "적용 뒤 실행할 full repository verifier가 필요합니다."
            )
        try:
            current_baseline = build_project_baseline(self._source_root)
            if current_baseline != proposal.result.project_baseline:
                raise _StaleSourceError("안전한 project source 문맥이 달라졌어.")
            prepared = self._prepare_changes(proposal)
        except (ProjectBaselineError, _StaleSourceError) as error:
            self._proposals.mark_stale(proposal_id, reason=str(error))
            raise ChangeAdoptionError(
                f"수정안을 만든 뒤 원본이 달라져 적용하지 않았어. {error} "
                "현재 내용을 기준으로 다시 수정안을 만들어야 해."
            ) from error

        snapshots = tuple(
            ChangeFileSnapshot(
                proposal_id=proposal_id,
                path=item.path,
                before_content=item.before,
                before_sha256=(
                    _sha256(item.before) if item.before is not None else None
                ),
                after_sha256=_sha256(item.after),
            )
            for item in prepared
        )
        try:
            self._proposals.start_application(proposal_id, snapshots)
        except ChangeProposalError as error:
            raise ChangeAdoptionError(str(error)) from error

        try:
            for item in prepared:
                self._atomic_write(item.target, item.after)
            self._verify_bytes(prepared, expected="after")
            verification_reason = self._verify_repository()
        except (OSError, ChangeAdoptionError) as error:
            recovery_error = self._restore_before(prepared)
            reason = f"적용 중 오류: {error}"
            if recovery_error is not None:
                reason += f"; 원본 복원 오류: {recovery_error}"
            try:
                self._proposals.finish_application(
                    proposal_id,
                    succeeded=False,
                    reason=reason,
                )
            except ChangeProposalError as state_error:
                reason += f"; 상태 기록 오류: {state_error}"
            if recovery_error is not None:
                raise ChangeAdoptionError(
                    "수정 적용과 원본 복원에 실패했어. 수동 복구가 필요해. " + reason
                ) from error
            raise ChangeAdoptionError(
                "수정 적용에 실패해서 변경된 파일을 적용 전 상태로 복원했어. "
                + reason
            ) from error

        try:
            self._proposals.finish_application(
                proposal_id,
                succeeded=True,
                reason=verification_reason,
            )
        except ChangeProposalError as error:
            recovery_error = self._restore_before(prepared)
            if recovery_error is None:
                try:
                    self._proposals.finish_application(
                        proposal_id,
                        succeeded=False,
                        reason="적용 완료 상태 저장 실패 후 source를 복원함",
                    )
                except ChangeProposalError:
                    pass
            raise ChangeAdoptionError(
                "수정 상태를 안전하게 저장하지 못해 source를 복원했어."
            ) from error

        paths = tuple(item.path for item in prepared)
        return ChangeAdoptionResult(
            proposal_id=proposal_id,
            status="applied",
            changed_paths=paths,
            message=(
                f"수정안 {proposal_id}를 실제 코드에 적용했어. "
                f"변경 파일은 {', '.join(paths)}야. 적용 직전 상태도 보관해서 "
                "이 파일들이 다시 바뀌지 않았다면 되돌릴 수 있어."
            ),
        )

    def rollback(self, proposal_id: str) -> ChangeAdoptionResult:
        self._require_proposal(proposal_id, "applied")
        snapshots = self._proposals.list_apply_snapshots(proposal_id)
        if not snapshots:
            raise ChangeAdoptionError("되돌릴 적용 전 snapshot을 찾지 못했습니다.")
        current_after: list[_PreparedChange] = []
        for snapshot in snapshots:
            target = self._safe_target(snapshot.path)
            current = self._read_regular_file(target)
            if current is None or _sha256(current) != snapshot.after_sha256:
                raise ChangeAdoptionError(
                    f"{snapshot.path} 파일이 적용 후 다시 변경되어 자동으로 덮어쓰지 않았어."
                )
            current_after.append(
                _PreparedChange(
                    path=snapshot.path,
                    target=target,
                    before=snapshot.before_content,
                    after=current,
                )
            )
        try:
            self._proposals.start_rollback(proposal_id)
        except ChangeProposalError as error:
            raise ChangeAdoptionError(str(error)) from error

        try:
            recovery_error = self._restore_before(tuple(current_after))
            if recovery_error is not None:
                raise recovery_error
            self._verify_bytes(tuple(current_after), expected="before")
        except (OSError, ChangeAdoptionError) as error:
            reapply_error = self._restore_after(tuple(current_after))
            reason = f"rollback 중 오류: {error}"
            if reapply_error is not None:
                reason += f"; 적용 상태 복원 오류: {reapply_error}"
            try:
                self._proposals.finish_rollback(
                    proposal_id,
                    succeeded=False,
                    reason=reason,
                )
            except ChangeProposalError:
                pass
            raise ChangeAdoptionError(
                "rollback에 실패했어. "
                + (
                    "적용 상태도 완전히 복구하지 못했어. 수동 확인이 필요해. "
                    if reapply_error is not None
                    else "적용 상태는 다시 복원했어. "
                )
                + reason
            ) from error

        try:
            self._proposals.finish_rollback(proposal_id, succeeded=True)
        except ChangeProposalError as error:
            self._restore_after(tuple(current_after))
            raise ChangeAdoptionError(
                "rollback 상태를 저장하지 못해 적용된 상태로 되돌렸어."
            ) from error
        paths = tuple(item.path for item in current_after)
        return ChangeAdoptionResult(
            proposal_id=proposal_id,
            status="rolled_back",
            changed_paths=paths,
            message=(
                f"수정안 {proposal_id}를 적용 전 상태로 되돌렸어. "
                f"복원한 범위는 {', '.join(paths)}야."
            ),
        )

    def recover(self, proposal_id: str) -> ChangeAdoptionResult:
        """Reconcile a persisted apply/rollback state without replaying the patch."""
        try:
            proposal = self._proposals.get(proposal_id)
            snapshots = self._proposals.list_apply_snapshots(proposal_id)
        except ChangeProposalError as error:
            raise ChangeAdoptionError(str(error)) from error
        if proposal is None:
            raise ChangeAdoptionError("복구할 수정안을 찾지 못했습니다.")
        paths = tuple(item.path for item in snapshots)
        if proposal.status in {
            "applied",
            "apply_failed",
            "rolled_back",
            "rollback_failed",
        }:
            return ChangeAdoptionResult(
                proposal_id=proposal_id,
                status=proposal.status,
                changed_paths=paths,
                message="이미 종료된 복구 상태를 그대로 확인했어.",
            )
        if proposal.status not in {"applying", "rolling_back"}:
            raise ChangeAdoptionError(
                "수정안이 applying 또는 rolling_back일 때만 복구할 수 있습니다."
            )
        if not snapshots:
            raise ChangeAdoptionError(
                "복구 snapshot이 없어 자동으로 source를 변경하지 않았습니다."
            )
        prepared = self._recovery_changes(proposal, snapshots)
        states = tuple(self._known_state(item) for item in prepared)
        if "unknown" in states:
            raise ChangeAdoptionError(
                "알 수 없는 target bytes가 있어 자동 복구하지 않았어. 수동 확인이 필요해."
            )
        if proposal.status == "applying":
            return self._recover_application(proposal_id, prepared, states)
        return self._recover_rollback(proposal_id, prepared)

    def _recover_application(
        self,
        proposal_id: str,
        prepared: tuple[_PreparedChange, ...],
        states: tuple[str, ...],
    ) -> ChangeAdoptionResult:
        paths = tuple(item.path for item in prepared)
        if all(state == "after" for state in states):
            try:
                reason = self._verify_repository()
            except ChangeAdoptionError as error:
                recovery_error = self._restore_before(prepared)
                detail = f"crash 복구 후 repository 검증 실패: {error}"
                if recovery_error is not None:
                    raise ChangeAdoptionError(
                        f"{detail}; source 복원도 실패해 수동 복구가 필요해: "
                        f"{recovery_error}"
                    ) from error
                self._proposals.finish_application(
                    proposal_id, succeeded=False, reason=detail
                )
                return ChangeAdoptionResult(
                    proposal_id=proposal_id,
                    status="apply_failed",
                    changed_paths=paths,
                    message="검증 실패로 적용 전 상태를 복원했어.",
                )
            self._proposals.finish_application(
                proposal_id,
                succeeded=True,
                reason=f"crash recovery; {reason}",
            )
            return ChangeAdoptionResult(
                proposal_id=proposal_id,
                status="applied",
                changed_paths=paths,
                message="적용 bytes와 검증 근거를 확인해 applied 상태를 복원했어.",
            )

        recovery_error = self._restore_before(prepared)
        if recovery_error is not None:
            raise ChangeAdoptionError(
                f"부분 적용 상태 복원에 실패해 수동 복구가 필요해: {recovery_error}"
            )
        self._proposals.finish_application(
            proposal_id,
            succeeded=False,
            reason="crash recovery restored all known before bytes",
        )
        return ChangeAdoptionResult(
            proposal_id=proposal_id,
            status="apply_failed",
            changed_paths=paths,
            message="완료되지 않은 적용을 감지해 모든 파일을 적용 전으로 복원했어.",
        )

    def _recover_rollback(
        self,
        proposal_id: str,
        prepared: tuple[_PreparedChange, ...],
    ) -> ChangeAdoptionResult:
        paths = tuple(item.path for item in prepared)
        recovery_error = self._restore_before(prepared)
        if recovery_error is not None:
            reapply_error = self._restore_after(prepared)
            reason = f"crash rollback 복구 실패: {recovery_error}"
            if reapply_error is not None:
                reason += f"; 적용 상태 복원 실패: {reapply_error}"
            self._proposals.finish_rollback(
                proposal_id,
                succeeded=False,
                reason=reason,
            )
            raise ChangeAdoptionError(reason)
        self._verify_bytes(prepared, expected="before")
        self._proposals.finish_rollback(
            proposal_id,
            succeeded=True,
            reason="crash recovery restored all before bytes",
        )
        return ChangeAdoptionResult(
            proposal_id=proposal_id,
            status="rolled_back",
            changed_paths=paths,
            message="중단된 rollback을 적용 전 상태로 복구했어.",
        )

    def _verify_repository(self) -> str:
        if self._repository_verifier is None:
            raise ChangeAdoptionError("full repository verifier가 없습니다.")
        try:
            source_before = build_project_baseline(self._source_root)
            results = self._repository_verifier.verify(self._source_root)
            source_after = build_project_baseline(self._source_root)
        except Exception as error:
            raise ChangeAdoptionError(
                f"full repository verifier 실행 실패: {type(error).__name__}"
            ) from error
        if source_after != source_before:
            raise ChangeAdoptionError(
                "full repository verifier가 project source를 변경했습니다."
            )
        if not results:
            raise ChangeAdoptionError("full repository verifier 결과가 없습니다.")
        summary = ", ".join(
            f"{item.name}:{item.status}:exit={item.exit_code}:"
            f"output={hashlib.sha256(item.output.encode('utf-8')).hexdigest()[:16]}"
            for item in results
        )
        if any(item.status != "pass" or item.exit_code != 0 for item in results):
            raise ChangeAdoptionError(f"full repository verification failed: {summary}")
        return f"full repository verification passed: {summary}"

    def _prepare_changes(
        self, proposal: StoredChangeProposal
    ) -> tuple[_PreparedChange, ...]:
        parsed = {item.path: item for item in _parse_patch(proposal.result.patch)}
        expected = {item.path: item for item in proposal.result.changed_files}
        if set(parsed) != set(expected):
            raise ChangeAdoptionError("patch 파일 범위가 저장된 수정안과 다릅니다.")
        prepared: list[_PreparedChange] = []
        for path, changed in expected.items():
            target = self._safe_target(path)
            before = self._read_regular_file(target)
            if changed.change_type == "added":
                if changed.before_sha256 is not None:
                    raise ChangeAdoptionError("추가 파일에 잘못된 before hash가 있습니다.")
                if before is not None:
                    raise _StaleSourceError(f"{path} 파일이 이미 존재해.")
                base = b""
            elif changed.change_type == "modified":
                if before is None:
                    raise _StaleSourceError(f"{path} 파일이 없어졌어.")
                if _sha256(before) != changed.before_sha256:
                    raise _StaleSourceError(f"{path} 내용이 달라졌어.")
                base = before
            else:
                raise ChangeAdoptionError("지원하지 않는 파일 변경 종류입니다.")
            try:
                after = _apply_file_patch(base, parsed[path])
            except (UnicodeError, ValueError) as error:
                raise ChangeAdoptionError(
                    f"patch를 안전하게 해석하지 못했습니다: {path}"
                ) from error
            if _sha256(after) != changed.after_sha256:
                raise ChangeAdoptionError(f"patch 결과 hash가 수정안과 다릅니다: {path}")
            prepared.append(_PreparedChange(path, target, before, after))
        return tuple(prepared)

    def _recovery_changes(
        self,
        proposal: StoredChangeProposal,
        snapshots: tuple[ChangeFileSnapshot, ...],
    ) -> tuple[_PreparedChange, ...]:
        parsed = {item.path: item for item in _parse_patch(proposal.result.patch)}
        expected = {item.path: item for item in proposal.result.changed_files}
        snapshot_by_path = {item.path: item for item in snapshots}
        if set(parsed) != set(expected) or set(parsed) != set(snapshot_by_path):
            raise ChangeAdoptionError(
                "복구 snapshot, patch와 수정 파일 범위가 일치하지 않습니다."
            )
        prepared: list[_PreparedChange] = []
        for path, changed in expected.items():
            snapshot = snapshot_by_path[path]
            before = snapshot.before_content
            if before is None:
                if changed.before_sha256 is not None:
                    raise ChangeAdoptionError("복구할 before bytes가 없습니다.")
                base = b""
            else:
                if _sha256(before) != changed.before_sha256:
                    raise ChangeAdoptionError("복구 before hash가 수정안과 다릅니다.")
                base = before
            try:
                after = _apply_file_patch(base, parsed[path])
            except (UnicodeError, ValueError) as error:
                raise ChangeAdoptionError(
                    f"복구 patch를 안전하게 해석하지 못했습니다: {path}"
                ) from error
            if (
                _sha256(after) != changed.after_sha256
                or _sha256(after) != snapshot.after_sha256
            ):
                raise ChangeAdoptionError("복구 after hash가 수정안과 다릅니다.")
            prepared.append(
                _PreparedChange(path, self._safe_target(path), before, after)
            )
        return tuple(prepared)

    def _known_state(self, item: _PreparedChange) -> str:
        current = self._read_regular_file(item.target)
        if current == item.before:
            return "before"
        if current == item.after:
            return "after"
        return "unknown"

    def _safe_target(self, value: str) -> Path:
        if not value or "\\" in value:
            raise ChangeAdoptionError("수정안에 안전하지 않은 경로가 있습니다.")
        relative = PurePosixPath(value)
        if (
            relative.is_absolute()
            or any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts)
            or not is_safe_project_path(Path(value))
        ):
            raise ChangeAdoptionError("수정안에 안전하지 않은 경로가 있습니다.")
        target = self._source_root.joinpath(*relative.parts)
        parent = self._source_root
        for part in relative.parts[:-1]:
            parent = parent / part
            if parent.is_symlink() or not parent.is_dir():
                raise ChangeAdoptionError(
                    f"수정 대상의 상위 directory가 없거나 안전하지 않습니다: {value}"
                )
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ChangeAdoptionError(f"수정 대상이 일반 파일이 아닙니다: {value}")
        return target

    @staticmethod
    def _read_regular_file(target: Path) -> bytes | None:
        if not target.exists():
            return None
        try:
            return target.read_bytes()
        except OSError as error:
            raise ChangeAdoptionError(f"수정 대상 파일을 읽지 못했습니다: {target}") from error

    def _atomic_write(self, target: Path, content: bytes) -> None:
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".winter-apply-", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._replace_file(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _restore_before(
        self, prepared: tuple[_PreparedChange, ...]
    ) -> OSError | ChangeAdoptionError | None:
        try:
            for item in prepared:
                if item.before is None:
                    if item.target.exists():
                        item.target.unlink()
                else:
                    self._atomic_write_direct(item.target, item.before)
            return None
        except (OSError, ChangeAdoptionError) as error:
            return error

    def _restore_after(
        self, prepared: tuple[_PreparedChange, ...]
    ) -> OSError | ChangeAdoptionError | None:
        try:
            for item in prepared:
                self._atomic_write_direct(item.target, item.after)
            return None
        except (OSError, ChangeAdoptionError) as error:
            return error

    @staticmethod
    def _atomic_write_direct(target: Path, content: bytes) -> None:
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".winter-recovery-", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _verify_bytes(
        prepared: tuple[_PreparedChange, ...], *, expected: str
    ) -> None:
        for item in prepared:
            if expected == "after":
                wanted = item.after
                if not item.target.is_file() or item.target.read_bytes() != wanted:
                    raise ChangeAdoptionError(f"적용 결과가 일치하지 않습니다: {item.path}")
            else:
                if item.before is None:
                    if item.target.exists():
                        raise ChangeAdoptionError(
                            f"추가 파일 rollback 결과가 일치하지 않습니다: {item.path}"
                        )
                elif not item.target.is_file() or item.target.read_bytes() != item.before:
                    raise ChangeAdoptionError(
                        f"rollback 결과가 일치하지 않습니다: {item.path}"
                    )

    def _require_proposal(
        self, proposal_id: str, expected_status: str
    ) -> StoredChangeProposal:
        try:
            proposal = self._proposals.get(proposal_id)
        except ChangeProposalError as error:
            raise ChangeAdoptionError(str(error)) from error
        if proposal is None:
            raise ChangeAdoptionError("수정안을 찾지 못했습니다.")
        if proposal.status != expected_status:
            raise ChangeAdoptionError(
                f"수정안이 {expected_status} 상태일 때만 이 작업을 할 수 있습니다."
            )
        return proposal


def _parse_patch(patch: str) -> tuple[_FilePatch, ...]:
    lines = patch.splitlines(keepends=True)
    parsed: list[_FilePatch] = []
    index = 0
    while index < len(lines):
        old_header = lines[index]
        if not old_header.startswith("--- a/"):
            raise ValueError("missing old file header")
        path = old_header[len("--- a/") :].rstrip("\n")
        index += 1
        if index >= len(lines) or lines[index].rstrip("\n") != f"+++ b/{path}":
            raise ValueError("mismatched new file header")
        index += 1
        hunks: list[_PatchHunk] = []
        while index < len(lines) and lines[index].startswith("@@ "):
            match = _HUNK_HEADER.match(lines[index])
            if match is None:
                raise ValueError("invalid hunk header")
            old_start, old_count, new_start, new_count = (
                int(match.group(1)),
                int(match.group(2) or "1"),
                int(match.group(3)),
                int(match.group(4) or "1"),
            )
            index += 1
            body: list[tuple[str, str]] = []
            consumed_old = 0
            consumed_new = 0
            while consumed_old < old_count or consumed_new < new_count:
                if index >= len(lines) or not lines[index]:
                    raise ValueError("truncated hunk")
                marker = lines[index][0]
                content = lines[index][1:]
                if marker not in {" ", "+", "-"}:
                    raise ValueError("invalid hunk line")
                if marker in {" ", "-"}:
                    consumed_old += 1
                if marker in {" ", "+"}:
                    consumed_new += 1
                if consumed_old > old_count or consumed_new > new_count:
                    raise ValueError("hunk line count exceeded")
                body.append((marker, content))
                index += 1
            hunks.append(
                _PatchHunk(old_start, old_count, new_start, new_count, tuple(body))
            )
        if not hunks:
            raise ValueError("file patch has no hunks")
        parsed.append(_FilePatch(path, tuple(hunks)))
    if len({item.path for item in parsed}) != len(parsed):
        raise ValueError("duplicate file patch")
    return tuple(parsed)


def _apply_file_patch(before: bytes, patch: _FilePatch) -> bytes:
    source = before.decode("utf-8").splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    for hunk in patch.hunks:
        start = max(hunk.old_start - 1, 0)
        if start < cursor or start > len(source):
            raise ValueError("invalid hunk position")
        output.extend(source[cursor:start])
        cursor = start
        produced = 0
        for marker, content in hunk.lines:
            if marker in {" ", "-"}:
                if cursor >= len(source) or source[cursor] != content:
                    raise ValueError("patch context does not match")
                cursor += 1
            if marker in {" ", "+"}:
                output.append(content)
                produced += 1
        if produced != hunk.new_count:
            raise ValueError("new hunk count does not match")
    output.extend(source[cursor:])
    return "".join(output).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
