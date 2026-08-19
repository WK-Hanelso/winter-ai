from pathlib import Path

import pytest

from companion.change_proposal import (
    ChangeProposalError,
    PersistentIsolatedWorkService,
    SqliteChangeProposalRepository,
    build_change_explanation,
)
from companion.isolated_work import (
    ChangedFile,
    ImplementationAuthorization,
    IsolatedWorkResult,
    VerificationResult,
)
from companion.project_baseline import build_project_baseline


def _authorization() -> ImplementationAuthorization:
    return ImplementationAuthorization(
        authorization_id="auth-add-fix",
        status="approved",
        objective="add 함수가 두 정수의 합을 반환하도록 고친다.",
        allowed_paths=("src/calc.py",),
        verification_profile="python_unittest",
        max_changed_lines=2,
    )


def _result(*, passed: bool = True) -> IsolatedWorkResult:
    patch = (
        "--- a/src/calc.py\n"
        "+++ b/src/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(left, right):\n"
        "-    return left - right\n"
        "+    return left + right\n"
    )
    return IsolatedWorkResult(
        proposal_id="proposal-add-fix",
        authorization_id="auth-add-fix",
        status="awaiting_adoption" if passed else "verification_failed",
        summary="add 함수의 연산자를 수정했다.",
        changed_files=(
            ChangedFile(
                path="src/calc.py",
                change_type="modified",
                before_sha256="a" * 64,
                after_sha256="b" * 64,
            ),
        ),
        patch=patch,
        verifications=(
            VerificationResult(
                name="python_unittest",
                status="pass" if passed else "fail",
                exit_code=0 if passed else 1,
                output="Ran 2 tests\nOK" if passed else "FAILED",
            ),
            VerificationResult(
                name="python_structure",
                status="pass",
                exit_code=0,
                output="Python structure check passed.",
            ),
        ),
        limitations=("실제 Winter 전체 회귀는 아직 실행하지 않았다.",),
        worker="codex-isolated-work",
        model="gpt-5.4",
    )


def test_explanation_leads_with_effect_and_separates_fact_from_limit() -> None:
    result = _result()

    explanation = build_change_explanation(_authorization(), result)

    assert explanation.headline == "수정안을 만들었고 현재 검증 범위는 통과했어."
    assert explanation.purpose == "add 함수가 두 정수의 합을 반환하도록 고친다."
    assert explanation.changes == (
        "src/calc.py에서 `return left - right`를 `return left + right`로 바꿨어.",
    )
    assert explanation.verification == (
        "python_unittest: 통과",
        "python_structure: 통과",
    )
    assert explanation.risks == (
        "현재 확인은 python_unittest, python_structure 범위까지야.",
    )
    assert explanation.worker_notes == (
        "실제 Winter 전체 회귀는 아직 실행하지 않았다.",
    )
    assert explanation.recommendation == "adoptable"
    assert explanation.adoption_question == "이 수정안을 실제 코드에 적용할까?"
    assert result.proposal_id in explanation.message
    assert "아직 실제 코드에는 적용하지 않았어" in explanation.message


def test_failed_result_is_explained_but_never_offered_for_adoption() -> None:
    explanation = build_change_explanation(_authorization(), _result(passed=False))

    assert explanation.recommendation == "revise"
    assert explanation.adoption_question is None
    assert "검증을 통과하지 못했어" in explanation.headline
    assert "python_unittest: 실패" in explanation.verification
    assert "적용하면 안 돼" in explanation.message


def test_repository_preserves_same_explanation_and_target_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "change-proposals.sqlite"
    repository = SqliteChangeProposalRepository(database)
    stored = repository.record(_authorization(), _result())

    reopened = SqliteChangeProposalRepository(database)
    loaded = reopened.get(stored.proposal_id)

    assert loaded == stored
    assert reopened.latest_awaiting_adoption() == stored
    assert loaded.patch_sha256 if loaded is not None else False
    assert loaded is not None
    assert loaded.explanation.adoption_question == "이 수정안을 실제 코드에 적용할까?"
    assert reopened.list_events(stored.proposal_id)[0].status == "awaiting_adoption"


def test_repository_preserves_privacy_safe_project_baseline(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = source / "src" / "calc.py"
    target.parent.mkdir(parents=True)
    target.write_text("return left - right\n", encoding="utf-8")
    result = _result()
    result = IsolatedWorkResult(
        **{
            **result.__dict__,
            "project_baseline": build_project_baseline(source),
        }
    )
    path = tmp_path / "proposals.sqlite"
    stored = SqliteChangeProposalRepository(path).record(_authorization(), result)

    reopened = SqliteChangeProposalRepository(path).get(stored.proposal_id)

    assert reopened == stored
    assert reopened is not None
    assert reopened.result.project_baseline == result.project_baseline


def test_repository_rejects_reusing_proposal_id_with_different_patch(
    tmp_path: Path,
) -> None:
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    repository.record(_authorization(), _result())
    changed = _result()
    changed = IsolatedWorkResult(
        **{**changed.__dict__, "patch": changed.patch + "# unexpected\n"}
    )

    with pytest.raises(ChangeProposalError, match="다른 내용"):
        repository.record(_authorization(), changed)


def test_adoption_approval_is_bound_to_exact_patch_and_does_not_apply_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "calc.py"
    source.write_text("return left - right\n", encoding="utf-8")
    original = source.read_bytes()
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    stored = repository.record(_authorization(), _result())

    with pytest.raises(ChangeProposalError, match="hash"):
        repository.record_decision(
            stored.proposal_id,
            decision="adopt",
            expected_patch_sha256="wrong",
        )
    approved = repository.record_decision(
        stored.proposal_id,
        decision="adopt",
        expected_patch_sha256=stored.patch_sha256,
    )

    assert approved.status == "adoption_approved"
    assert source.read_bytes() == original
    assert [event.status for event in repository.list_events(stored.proposal_id)] == [
        "awaiting_adoption",
        "adoption_approved",
    ]


def test_failed_proposal_cannot_be_approved(tmp_path: Path) -> None:
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    stored = repository.record(_authorization(), _result(passed=False))

    with pytest.raises(ChangeProposalError, match="채택 승인"):
        repository.record_decision(
            stored.proposal_id,
            decision="adopt",
            expected_patch_sha256=stored.patch_sha256,
        )


def test_revision_request_is_persisted_without_changing_patch(tmp_path: Path) -> None:
    repository = SqliteChangeProposalRepository(tmp_path / "proposals.sqlite")
    stored = repository.record(_authorization(), _result())

    revised = repository.record_decision(
        stored.proposal_id,
        decision="revise",
        reason="중복 함수가 생기지 않는지 더 확인해줘.",
    )

    assert revised.status == "revision_requested"
    assert revised.result.patch == stored.result.patch
    assert repository.latest_awaiting_adoption() is None
    assert repository.list_events(stored.proposal_id)[-1].reason == (
        "중복 함수가 생기지 않는지 더 확인해줘."
    )


def test_persistent_service_stores_worker_result_before_returning(tmp_path: Path) -> None:
    class Worker:
        def execute(
            self, authorization: ImplementationAuthorization
        ) -> IsolatedWorkResult:
            assert authorization == _authorization()
            return _result()

    database = tmp_path / "proposals.sqlite"
    stored = PersistentIsolatedWorkService(
        Worker(),
        SqliteChangeProposalRepository(database),
    ).execute(_authorization())

    assert stored.status == "awaiting_adoption"
    assert stored.explanation.message.startswith(
        "수정안을 만들었고 현재 검증 범위는 통과했어."
    )
    assert SqliteChangeProposalRepository(database).get(stored.proposal_id) == stored


def test_explanation_does_not_repeat_untrusted_worker_summary() -> None:
    result = _result()
    result = IsolatedWorkResult(
        **{
            **result.__dict__,
            "summary": "실제 배포까지 끝냈고 모든 계정 권한도 확보했다.",
        }
    )

    explanation = build_change_explanation(_authorization(), result)

    assert "배포" not in explanation.message
    assert "계정 권한" not in explanation.message
