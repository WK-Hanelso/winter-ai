from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion.change_adoption import (
    ChangeAdoptionError,
    ChangeAdoptionService,
    CommandRepositoryVerifier,
    VerificationCommand,
)
from companion.change_proposal import ChangeFileSnapshot, SqliteChangeProposalRepository
from companion.isolated_work import (
    ChangedFile,
    ImplementationAuthorization,
    IsolatedWorkResult,
    VerificationResult,
)
from companion.project_baseline import build_project_baseline


class _Verifier:
    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed
        self.calls = 0

    def verify(self, source_root: Path) -> tuple[VerificationResult, ...]:
        assert source_root.is_dir()
        self.calls += 1
        return (
            VerificationResult(
                name="full_repository",
                status="pass" if self.passed else "fail",
                exit_code=0 if self.passed else 1,
                output="OK" if self.passed else "FAILED",
            ),
        )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store_proposal(
    source: Path,
    repository: SqliteChangeProposalRepository,
    changes: dict[str, tuple[str | None, str]],
) -> tuple[str, str]:
    patch_parts: list[str] = []
    changed_files: list[ChangedFile] = []
    for relative, (before, after) in sorted(changes.items()):
        before_bytes = before.encode() if before is not None else None
        after_bytes = after.encode()
        patch_parts.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True) if before is not None else [],
                after.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        changed_files.append(
            ChangedFile(
                path=relative,
                change_type="modified" if before is not None else "added",
                before_sha256=_sha(before_bytes) if before_bytes is not None else None,
                after_sha256=_sha(after_bytes),
            )
        )
    authorization = ImplementationAuthorization(
        authorization_id="auth-adoption-test",
        status="approved",
        objective="승인된 계산 수정안을 적용한다.",
        allowed_paths=tuple(changes),
        verification_profile="python_unittest",
        max_changed_lines=20,
    )
    result = IsolatedWorkResult(
        proposal_id="proposal-adoption-test",
        authorization_id=authorization.authorization_id,
        status="awaiting_adoption",
        summary="수정했다.",
        changed_files=tuple(changed_files),
        patch="".join(patch_parts),
        verifications=(
            VerificationResult(
                name="python_unittest",
                status="pass",
                exit_code=0,
                output="OK",
            ),
        ),
        limitations=(),
        worker="fake",
        model="fake",
        project_baseline=build_project_baseline(source),
    )
    stored = repository.record(authorization, result)
    repository.record_decision(
        stored.proposal_id,
        decision="adopt",
        expected_patch_sha256=stored.patch_sha256,
    )
    return stored.proposal_id, stored.patch_sha256


def _start_application_without_writes(
    source: Path,
    repository: SqliteChangeProposalRepository,
    proposal_id: str,
) -> None:
    proposal = repository.get(proposal_id)
    assert proposal is not None
    snapshots = tuple(
        ChangeFileSnapshot(
            proposal_id=proposal_id,
            path=changed.path,
            before_content=(
                (source / changed.path).read_bytes()
                if (source / changed.path).exists()
                else None
            ),
            before_sha256=changed.before_sha256,
            after_sha256=changed.after_sha256,
        )
        for changed in proposal.result.changed_files
    )
    repository.start_application(proposal_id, snapshots)


def test_apply_changes_only_an_exact_approved_baseline(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {"src/calc.py": ("return left - right\n", "return left + right\n")},
    )

    result = ChangeAdoptionService(
        source, repository, repository_verifier=_Verifier()
    ).apply(
        proposal_id,
        expected_patch_sha256=patch_sha256,
    )

    assert result.status == "applied"
    assert result.changed_paths == ("src/calc.py",)
    assert "실제 코드에 적용했어" in result.message
    assert "되돌릴 수 있어" in result.message
    assert target.read_text(encoding="utf-8") == "return left + right\n"
    assert repository.get(proposal_id).status == "applied"  # type: ignore[union-attr]
    assert [event.status for event in repository.list_events(proposal_id)] == [
        "awaiting_adoption",
        "adoption_approved",
        "applying",
        "applied",
    ]


def test_apply_refuses_a_stale_source_without_overwriting_it(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {"src/calc.py": ("return left - right\n", "return left + right\n")},
    )
    target.write_text("return left * right\n", encoding="utf-8")

    with pytest.raises(ChangeAdoptionError, match="달라져"):
        ChangeAdoptionService(
            source, repository, repository_verifier=_Verifier()
        ).apply(
            proposal_id,
            expected_patch_sha256=patch_sha256,
        )

    assert target.read_text(encoding="utf-8") == "return left * right\n"
    assert repository.get(proposal_id).status == "stale"  # type: ignore[union-attr]
    assert repository.list_events(proposal_id)[-1].status == "stale"


def test_apply_restores_every_file_when_a_replace_fails(tmp_path: Path) -> None:
    source = tmp_path / "source"
    first = source / "src" / "first.py"
    second = source / "src" / "second.py"
    first.parent.mkdir(parents=True)
    first.write_text("value = 1\n", encoding="utf-8")
    second.write_text("value = 2\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {
            "src/first.py": ("value = 1\n", "value = 10\n"),
            "src/second.py": ("value = 2\n", "value = 20\n"),
        },
    )
    calls = 0

    def fail_second_replace(staged: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        staged.replace(destination)

    with pytest.raises(ChangeAdoptionError, match="복원"):
        ChangeAdoptionService(
            source,
            repository,
            replace_file=fail_second_replace,
            repository_verifier=_Verifier(),
        ).apply(proposal_id, expected_patch_sha256=patch_sha256)

    assert first.read_text(encoding="utf-8") == "value = 1\n"
    assert second.read_text(encoding="utf-8") == "value = 2\n"
    assert repository.get(proposal_id).status == "apply_failed"  # type: ignore[union-attr]


def test_rollback_survives_restart_and_restores_modified_and_added_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    added = source / "src" / "message.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    database = tmp_path / "proposals.sqlite"
    repository = SqliteChangeProposalRepository(database)
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {
            "src/calc.py": ("return left - right\n", "return left + right\n"),
            "src/message.py": (None, 'MESSAGE = "fixed"\n'),
        },
    )
    ChangeAdoptionService(
        source, repository, repository_verifier=_Verifier()
    ).apply(
        proposal_id,
        expected_patch_sha256=patch_sha256,
    )

    reopened = SqliteChangeProposalRepository(database)
    result = ChangeAdoptionService(source, reopened).rollback(proposal_id)

    assert result.status == "rolled_back"
    assert "적용 전 상태로 되돌렸어" in result.message
    assert target.read_text(encoding="utf-8") == "return left - right\n"
    assert not added.exists()
    assert reopened.get(proposal_id).status == "rolled_back"  # type: ignore[union-attr]


def test_rollback_refuses_to_erase_changes_made_after_application(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {"src/calc.py": ("return left - right\n", "return left + right\n")},
    )
    service = ChangeAdoptionService(
        source, repository, repository_verifier=_Verifier()
    )
    service.apply(proposal_id, expected_patch_sha256=patch_sha256)
    target.write_text("return 42\n", encoding="utf-8")

    with pytest.raises(ChangeAdoptionError, match="적용 후 다시 변경"):
        service.rollback(proposal_id)

    assert target.read_text(encoding="utf-8") == "return 42\n"
    assert repository.get(proposal_id).status == "applied"  # type: ignore[union-attr]


def test_apply_rejects_non_target_project_drift(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    context = source / "docs" / "contract.md"
    target.parent.mkdir(parents=True)
    context.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    context.write_text("v1\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {"src/calc.py": ("return left - right\n", "return left + right\n")},
    )
    context.write_text("v2\n", encoding="utf-8")

    with pytest.raises(ChangeAdoptionError, match="project source"):
        ChangeAdoptionService(
            source, repository, repository_verifier=_Verifier()
        ).apply(proposal_id, expected_patch_sha256=patch_sha256)

    assert target.read_text(encoding="utf-8") == "return left - right\n"
    assert repository.get(proposal_id).status == "stale"  # type: ignore[union-attr]


def test_apply_requires_explicit_full_repository_verifier(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {"src/calc.py": ("return left - right\n", "return left + right\n")},
    )

    with pytest.raises(ChangeAdoptionError, match="verifier"):
        ChangeAdoptionService(source, repository).apply(
            proposal_id, expected_patch_sha256=patch_sha256
        )

    assert target.read_text(encoding="utf-8") == "return left - right\n"
    assert repository.get(proposal_id).status == "adoption_approved"  # type: ignore[union-attr]


def test_post_apply_verification_failure_restores_all_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {"src/calc.py": ("return left - right\n", "return left + right\n")},
    )

    with pytest.raises(ChangeAdoptionError, match="verification failed"):
        ChangeAdoptionService(
            source,
            repository,
            repository_verifier=_Verifier(passed=False),
        ).apply(proposal_id, expected_patch_sha256=patch_sha256)

    assert target.read_text(encoding="utf-8") == "return left - right\n"
    stored = repository.get(proposal_id)
    assert stored is not None and stored.status == "apply_failed"
    reason = repository.list_events(proposal_id)[-1].reason or ""
    assert "full repository verification failed" in reason
    assert "output=" in reason
    assert "FAILED" not in reason


def test_apply_refuses_a_target_outside_the_safe_project_surface(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("safe baseline\n", encoding="utf-8")
    target = source / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {"notes.txt": ("before\n", "after\n")},
    )

    with pytest.raises(ChangeAdoptionError, match="안전하지 않은 경로"):
        ChangeAdoptionService(
            source, repository, repository_verifier=_Verifier()
        ).apply(proposal_id, expected_patch_sha256=patch_sha256)

    assert target.read_text(encoding="utf-8") == "before\n"
    assert repository.get(proposal_id).status == "adoption_approved"  # type: ignore[union-attr]


def test_recover_applying_all_after_and_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, _ = _store_proposal(
        source,
        repository,
        {"src/calc.py": ("return left - right\n", "return left + right\n")},
    )
    _start_application_without_writes(source, repository, proposal_id)
    target.write_text("return left + right\n", encoding="utf-8")
    verifier = _Verifier()
    service = ChangeAdoptionService(
        source, repository, repository_verifier=verifier
    )

    recovered = service.recover(proposal_id)
    repeated = service.recover(proposal_id)

    assert recovered.status == "applied"
    assert repeated.status == "applied"
    assert verifier.calls == 1
    assert [event.status for event in repository.list_events(proposal_id)][-2:] == [
        "applying",
        "applied",
    ]


@pytest.mark.parametrize("state", ("all_before", "mixed"))
def test_recover_applying_known_incomplete_state_restores_before(
    tmp_path: Path, state: str
) -> None:
    source = tmp_path / "source"
    first = source / "src" / "first.py"
    second = source / "src" / "second.py"
    first.parent.mkdir(parents=True)
    first.write_text("value = 1\n", encoding="utf-8")
    second.write_text("value = 2\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, _ = _store_proposal(
        source,
        repository,
        {
            "src/first.py": ("value = 1\n", "value = 10\n"),
            "src/second.py": ("value = 2\n", "value = 20\n"),
        },
    )
    _start_application_without_writes(source, repository, proposal_id)
    if state == "mixed":
        first.write_text("value = 10\n", encoding="utf-8")

    result = ChangeAdoptionService(
        source, repository, repository_verifier=_Verifier()
    ).recover(proposal_id)

    assert result.status == "apply_failed"
    assert first.read_text(encoding="utf-8") == "value = 1\n"
    assert second.read_text(encoding="utf-8") == "value = 2\n"


def test_recover_refuses_unknown_target_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, _ = _store_proposal(
        source,
        repository,
        {"src/calc.py": ("return left - right\n", "return left + right\n")},
    )
    _start_application_without_writes(source, repository, proposal_id)
    target.write_text("return 42\n", encoding="utf-8")

    with pytest.raises(ChangeAdoptionError, match="수동 확인"):
        ChangeAdoptionService(
            source, repository, repository_verifier=_Verifier()
        ).recover(proposal_id)

    assert target.read_text(encoding="utf-8") == "return 42\n"
    assert repository.get(proposal_id).status == "applying"  # type: ignore[union-attr]


@pytest.mark.parametrize("state", ("all_after", "all_before", "mixed"))
def test_recover_rolling_back_known_state_completes_rollback(
    tmp_path: Path, state: str
) -> None:
    source = tmp_path / "source"
    first = source / "src" / "first.py"
    second = source / "src" / "second.py"
    first.parent.mkdir(parents=True)
    first.write_text("value = 1\n", encoding="utf-8")
    second.write_text("value = 2\n", encoding="utf-8")
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    proposal_id, patch_sha256 = _store_proposal(
        source,
        repository,
        {
            "src/first.py": ("value = 1\n", "value = 10\n"),
            "src/second.py": ("value = 2\n", "value = 20\n"),
        },
    )
    service = ChangeAdoptionService(
        source, repository, repository_verifier=_Verifier()
    )
    service.apply(proposal_id, expected_patch_sha256=patch_sha256)
    repository.start_rollback(proposal_id)
    if state in {"all_before", "mixed"}:
        first.write_text("value = 1\n", encoding="utf-8")
    if state == "all_before":
        second.write_text("value = 2\n", encoding="utf-8")

    recovered = service.recover(proposal_id)

    assert recovered.status == "rolled_back"
    assert first.read_text(encoding="utf-8") == "value = 1\n"
    assert second.read_text(encoding="utf-8") == "value = 2\n"


def test_command_verifier_runs_fixed_commands_without_shell_and_stops_on_failure(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> object:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0 if len(calls) == 1 else 1, stdout="", stderr="")

    results = CommandRepositoryVerifier(
        (
            VerificationCommand("pytest", ("python", "-m", "pytest", "-q")),
            VerificationCommand("ruff", ("ruff", "check", ".")),
            VerificationCommand("mypy", ("mypy", "src", "experiments")),
        ),
        runner=runner,
    ).verify(tmp_path)

    assert [item.name for item in results] == ["pytest", "ruff"]
    assert [item.status for item in results] == ["pass", "fail"]
    assert len(calls) == 2
    assert all("shell" not in kwargs for _, kwargs in calls)
    assert all(kwargs["cwd"] == tmp_path for _, kwargs in calls)
