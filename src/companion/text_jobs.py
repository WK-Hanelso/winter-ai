"""Approved Host jobs survive terminal exit; reuse the existing SQLite inbox."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from companion.adapters.codex_gateway_worker import CodexTypedProjectWorker
from companion.adapters.codex_worker import _minimal_environment
from companion.owned_work import OwnedWorkReceipt, OwnedWorkUpdate
from companion.owned_work_repository import SqliteOwnedWorkRepository
from companion.work_orchestration import WorkRequest
from companion.worker import ReadOnlyWorkerResult, WorkerUnavailableError


class ProcessWorkerQueue:
    def __init__(
        self, database: Path, source_root: Path, audit_dir: Path, *,
        executable: str = "codex", model: str = "gpt-5.6-sol",
        spawn: Callable[..., object] = subprocess.Popen,
    ) -> None:
        self.repository = SqliteOwnedWorkRepository(database, recovery="fail")
        self._database, self._source_root, self._audit_dir = database, source_root, audit_dir
        self._executable, self._model, self._spawn = executable, model, spawn
        self._children: list[tuple[str, object]] = []

    def submit(self, request: WorkRequest) -> OwnedWorkReceipt:
        job_id = uuid4().hex
        try:
            self.repository.enqueue(job_id, request)
        except RuntimeError as error:
            raise WorkerUnavailableError("진행 중인 작업 또는 저장 한도 초과") from error
        environment = _minimal_environment()
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        try:
            process = self._spawn(
                [sys.executable, "-m", "companion.text_jobs", "--database", str(self._database),
                 "--job-id", job_id, "--project-root", str(self._source_root),
                 "--audit-dir", str(self._audit_dir), "--codex-executable", self._executable,
                 "--codex-model", self._model],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True, env=environment,
            )
            self._children.append((job_id, process))
            if isinstance(process, subprocess.Popen):
                self.repository.assign_pending_owner(job_id, process.pid)
        except OSError as error:
            self.repository.complete(OwnedWorkUpdate(job_id, "failed", "작업을 시작하지 못했어."))
            raise WorkerUnavailableError("Host 작업 프로세스를 시작하지 못했습니다.") from error
        return OwnedWorkReceipt(job_id)

    def ready(self) -> tuple[OwnedWorkUpdate, ...]:
        # Reap our finished children without waiting or touching any unrelated process.
        alive = []
        for job_id, process in self._children:
            if isinstance(process, subprocess.Popen) and process.poll() is not None:
                if self.repository.status(job_id) in {"pending", "running"}:
                    self.repository.complete(OwnedWorkUpdate(
                        job_id, "failed", "작업 프로세스가 중단됐어. 자동으로 재실행하지 않았어.",
                    ))
                continue
            alive.append((job_id, process))
        self._children = alive
        return self.repository.ready()

    def acknowledge(self, update_id: str) -> None:
        self.repository.acknowledge(update_id)


def run_job(
    repository: SqliteOwnedWorkRepository, job_id: str,
    execute: Callable[[str], ReadOnlyWorkerResult],
) -> int:
    claimed = repository.claim_pending(expected_id=job_id)
    if claimed is None or claimed[0] != job_id:
        return 1
    try:
        result = execute(claimed[1].user_text)
        text = f"코덱스 확인 결과가 왔어. {result.answer}"
        if result.limitations:
            text += "\n확인 범위: " + " ".join(result.limitations)
        update = OwnedWorkUpdate(job_id, "partial" if result.limitations else "completed",
                                 text, result.evidence, result.limitations)
    except Exception:  # noqa: BLE001 - persist failure, never invent a successful result
        update = OwnedWorkUpdate(job_id, "failed", "코덱스 확인에 실패했어. 성공한 척하지 않을게.")
    repository.complete(update)
    return 0


def main() -> int:
    from companion.text_host import audited_execution

    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--codex-executable", required=True)
    parser.add_argument("--codex-model", required=True)
    args = parser.parse_args()
    worker = CodexTypedProjectWorker(
        args.project_root, executable=args.codex_executable, model=args.codex_model,
        allowed_roots=("README.md", "pyproject.toml", "src", "tests"),
    )
    return run_job(SqliteOwnedWorkRepository(args.database, recovery="none"), args.job_id,
                   lambda task: audited_execution(worker, task, args.audit_dir))


if __name__ == "__main__":
    raise SystemExit(main())
