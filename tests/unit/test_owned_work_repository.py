import multiprocessing
from pathlib import Path
import threading

import pytest

from companion.owned_work import AsyncOwnedWorkCoordinator, OwnedWorkUpdate
from companion.owned_work_repository import (
    OwnedWorkRepositoryError,
    SqliteOwnedWorkRepository,
)
from companion.work_orchestration import WorkContext, WorkRequest
from companion.worker import ReadOnlyWorkerResult


def test_sqlite_owned_work_survives_completion_and_delivery_ack(
    tmp_path: Path,
) -> None:
    database = tmp_path / "owned-work.sqlite"
    repository = SqliteOwnedWorkRepository(database)
    request = WorkRequest("현재 코드 latency를 진단해줘")
    repository.enqueue("job-1", request)

    assert repository.claim_pending() == ("job-1", request)
    repository.complete(
        OwnedWorkUpdate(
            id="job-1",
            status="partial",
            text="아직 세부 병목은 확정할 수 없어.",
            evidence=("docs/realtime.md:1",),
            limitations=("성공 turn만 기록돼 있어.",),
        )
    )

    reopened = SqliteOwnedWorkRepository(database)
    assert reopened.status("job-1") == "partial"
    assert reopened.ready()[0].evidence == ("docs/realtime.md:1",)
    reopened.acknowledge("job-1")
    assert SqliteOwnedWorkRepository(database).ready() == ()


def test_sqlite_owned_work_rejects_context_and_second_active_job(
    tmp_path: Path,
) -> None:
    repository = SqliteOwnedWorkRepository(tmp_path / "owned-work.sqlite")
    repository.enqueue("job-1", WorkRequest("코드 원인을 진단해줘"))

    with pytest.raises(OwnedWorkRepositoryError, match="already active"):
        repository.enqueue("job-2", WorkRequest("다른 코드도 진단해줘"))
    with pytest.raises(OwnedWorkRepositoryError, match="bounded current request"):
        SqliteOwnedWorkRepository(tmp_path / "other.sqlite").enqueue(
            "job-private",
            WorkRequest(
                "코드 원인을 진단해줘",
                (WorkContext("과거 대화", "winter_only"),),
            ),
        )


def test_opening_repository_does_not_requeue_a_live_process_job(
    tmp_path: Path,
) -> None:
    database = tmp_path / "owned-work.sqlite"
    first = SqliteOwnedWorkRepository(database)
    request = WorkRequest("코드 원인을 진단해줘")
    first.enqueue("job-live", request)
    assert first.claim_pending() == ("job-live", request)

    second = SqliteOwnedWorkRepository(database)

    assert second.status("job-live") == "running"
    assert second.claim_pending() is None


class _RestartExecutor:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, request: WorkRequest) -> ReadOnlyWorkerResult:
        self.started.set()
        assert self.release.wait(timeout=2)
        return ReadOnlyWorkerResult(
            answer="재시작 뒤 확인을 마쳤어.",
            evidence=("README.md:1",),
            limitations=(),
            worker="fake",
            model="fake",
        )


def _claim_then_exit(database: str) -> None:
    repository = SqliteOwnedWorkRepository(Path(database))
    assert repository.claim_pending() is not None


def test_coordinator_requeues_running_job_and_restores_result_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "owned-work.sqlite"
    before_crash = SqliteOwnedWorkRepository(database)
    request = WorkRequest("코드 원인을 진단해줘")
    before_crash.enqueue("job-restart", request)
    process = multiprocessing.get_context("spawn").Process(
        target=_claim_then_exit,
        args=(str(database),),
    )
    process.start()
    process.join(timeout=5)

    assert process.exitcode == 0
    assert before_crash.status("job-restart") == "running"

    executor = _RestartExecutor()
    recovered = SqliteOwnedWorkRepository(database)
    activity_seen = threading.Event()
    coordinator = AsyncOwnedWorkCoordinator(
        executor,
        repository=recovered,
        foreground_activity=activity_seen.set,
    )
    assert activity_seen.is_set()
    assert executor.started.wait(timeout=1)
    executor.release.set()
    assert coordinator.wait_for_idle(timeout=2)

    final_repository = SqliteOwnedWorkRepository(database)
    assert final_repository.status("job-restart") == "completed"
    assert final_repository.ready()[0].text == "재시작 뒤 확인을 마쳤어."
