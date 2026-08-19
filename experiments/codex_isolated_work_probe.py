"""Run a real Codex fix against a disposable synthetic project."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

from companion.adapters.codex_isolated_worker import CodexIsolatedWorkWorker
from companion.change_adoption import (
    ChangeAdoptionService,
    CommandRepositoryVerifier,
    VerificationCommand,
)
from companion.change_proposal import (
    PersistentIsolatedWorkService,
    SqliteChangeProposalRepository,
)
from companion.isolated_work import ImplementationAuthorization


def main() -> int:
    executable = shutil.which("codex")
    if executable is None:
        raise SystemExit("codex executable was not found")

    with TemporaryDirectory(prefix="winter-isolated-probe-source-") as temporary:
        root = Path(temporary)
        source = root / "source"
        (source / "src").mkdir(parents=True)
        (source / "tests").mkdir()
        broken = (
            "def add(left: int, right: int) -> int:\n"
            "    \"\"\"Return the sum of two integers.\"\"\"\n"
            "    return left - right\n"
        )
        source_file = source / "src" / "calc.py"
        source_file.write_text(broken, encoding="utf-8")
        (source / "tests" / "test_calc.py").write_text(
            "import unittest\n\n"
            "from src.calc import add\n\n\n"
            "class CalcTest(unittest.TestCase):\n"
            "    def test_adds_positive_numbers(self) -> None:\n"
            "        self.assertEqual(add(2, 3), 5)\n\n"
            "    def test_adds_negative_numbers(self) -> None:\n"
            "        self.assertEqual(add(-2, -3), -5)\n",
            encoding="utf-8",
        )
        authorization = ImplementationAuthorization(
            authorization_id="probe-addition-fix",
            status="approved",
            objective=(
                "src/calc.py의 add 함수가 문서와 테스트에 맞게 두 정수의 합을 "
                "반환하도록 최소 수정하고 로컬 테스트로 확인한다."
            ),
            allowed_paths=("src/calc.py",),
            verification_profile="python_unittest",
            max_changed_lines=2,
        )
        database = root / "change-proposals.sqlite"
        stored = PersistentIsolatedWorkService(
            CodexIsolatedWorkWorker(
                source,
                executable=executable,
                python_executable=sys.executable,
                timeout_seconds=600,
            ),
            SqliteChangeProposalRepository(database),
        ).execute(authorization)
        reopened = SqliteChangeProposalRepository(database).get(stored.proposal_id)
        source_unchanged_before_adoption = (
            source_file.read_text(encoding="utf-8") == broken
        )
        proposals = SqliteChangeProposalRepository(database)
        proposals.record_decision(
            stored.proposal_id,
            decision="adopt",
            expected_patch_sha256=stored.patch_sha256,
        )
        adoption = ChangeAdoptionService(
            source,
            proposals,
            repository_verifier=CommandRepositoryVerifier(
                (
                    VerificationCommand(
                        "python_unittest",
                        (
                            sys.executable,
                            "-m",
                            "unittest",
                            "discover",
                            "-s",
                            "tests",
                        ),
                    ),
                )
            ),
        )
        applied = adoption.apply(
            stored.proposal_id,
            expected_patch_sha256=stored.patch_sha256,
        )
        applied_source = source_file.read_text(encoding="utf-8")
        rolled_back = adoption.rollback(stored.proposal_id)
        source_restored_after_rollback = (
            source_file.read_text(encoding="utf-8") == broken
        )
        payload = {
            "proposal_id": stored.proposal_id,
            "status": stored.status,
            "patch_sha256": stored.patch_sha256,
            "result": asdict(stored.result),
            "winter_explanation": asdict(stored.explanation),
            "survived_repository_restart": reopened == stored,
            "source_unchanged_before_adoption": source_unchanged_before_adoption,
            "application": asdict(applied),
            "applied_expected_change": "return left + right" in applied_source,
            "rollback": asdict(rolled_back),
            "source_restored_after_rollback": source_restored_after_rollback,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return (
            0
            if stored.status == "awaiting_adoption"
            and reopened == stored
            and source_unchanged_before_adoption
            and applied.status == "applied"
            and "return left + right" in applied_source
            and rolled_back.status == "rolled_back"
            and source_restored_after_rollback
            else 1
        )


if __name__ == "__main__":
    raise SystemExit(main())
