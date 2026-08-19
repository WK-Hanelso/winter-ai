from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

import pytest

from companion.change_adoption import ChangeAdoptionError, ChangeAdoptionService
from companion.change_proposal import SqliteChangeProposalRepository
from companion.isolated_work import (
    ChangedFile,
    ImplementationAuthorization,
    IsolatedWorkResult,
    VerificationResult,
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
    )
    stored = repository.record(authorization, result)
    repository.record_decision(
        stored.proposal_id,
        decision="adopt",
        expected_patch_sha256=stored.patch_sha256,
    )
    return stored.proposal_id, stored.patch_sha256


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

    result = ChangeAdoptionService(source, repository).apply(
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
        ChangeAdoptionService(source, repository).apply(
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
    ChangeAdoptionService(source, repository).apply(
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
    service = ChangeAdoptionService(source, repository)
    service.apply(proposal_id, expected_patch_sha256=patch_sha256)
    target.write_text("return 42\n", encoding="utf-8")

    with pytest.raises(ChangeAdoptionError, match="적용 후 다시 변경"):
        service.rollback(proposal_id)

    assert target.read_text(encoding="utf-8") == "return 42\n"
    assert repository.get(proposal_id).status == "applied"  # type: ignore[union-attr]
