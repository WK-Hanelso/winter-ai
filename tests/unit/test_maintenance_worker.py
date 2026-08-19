from pathlib import Path
from threading import Event

import pytest

from companion.maintenance_worker import (
    MaintenanceWorkerAlreadyRunningError,
    ShadowMaintenanceWorker,
    SingleInstanceLock,
    main,
)
from companion.turn_understanding import (
    FakeTurnInterpreter,
    ShadowAnalysisService,
    SqliteTurnUnderstandingRepository,
    TurnInterpretationRequest,
    TurnInterpreterUnavailableError,
)


class UnavailableInterpreter:
    def interpret(self, request: TurnInterpretationRequest):  # type: ignore[no-untyped-def]
        raise TurnInterpreterUnavailableError("analysis server is offline")


def _repository(path: Path) -> SqliteTurnUnderstandingRepository:
    repository = SqliteTurnUnderstandingRepository(path)
    repository.enqueue(TurnInterpretationRequest("첫 발화"), source="voice")
    return repository


def test_worker_completes_pending_once_and_restart_does_not_duplicate(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "turns.sqlite")
    first_worker = ShadowMaintenanceWorker(
        ShadowAnalysisService(repository, FakeTurnInterpreter())
    )

    first = first_worker.run_once()
    restarted = ShadowMaintenanceWorker(
        ShadowAnalysisService(repository, FakeTurnInterpreter())
    ).run_once()

    assert (first.processed, first.completed, first.failed) == (1, 1, 0)
    assert (first.pending_before, first.pending_after) == (1, 0)
    assert first.elapsed_seconds >= 0
    assert first.next_delay_seconds == 0
    assert restarted.processed == 0
    assert (restarted.pending_before, restarted.pending_after) == (0, 0)
    assert restarted.next_delay_seconds == 2.0
    assert len(repository.list(status="completed")) == 1


def test_worker_backs_off_without_failing_pending_turn(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "turns.sqlite")
    worker = ShadowMaintenanceWorker(
        ShadowAnalysisService(repository, UnavailableInterpreter()),  # type: ignore[arg-type]
        unavailable_backoff_seconds=3,
        max_backoff_seconds=10,
    )

    first = worker.run_once()
    second = worker.run_once()
    third = worker.run_once()

    assert (first.deferred, first.next_delay_seconds) == (1, 3)
    assert (second.deferred, second.next_delay_seconds) == (1, 6)
    assert (third.deferred, third.next_delay_seconds) == (1, 10)
    assert len(repository.list(status="pending")) == 1
    assert repository.list(status="failed") == ()
    assert repository.list(status="pending")[0].defer_count == 3


def test_worker_serve_stops_cleanly_after_cycle(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "turns.sqlite")
    worker = ShadowMaintenanceWorker(
        ShadowAnalysisService(repository, FakeTurnInterpreter())
    )
    stop = Event()
    cycles = []

    def finish_after_first(cycle) -> None:  # type: ignore[no-untyped-def]
        cycles.append(cycle)
        stop.set()

    worker.serve(stop, on_cycle=finish_after_first)

    assert len(cycles) == 1
    assert cycles[0].completed == 1


def test_worker_can_pause_between_completed_background_items(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "turns.sqlite")
    worker = ShadowMaintenanceWorker(
        ShadowAnalysisService(repository, FakeTurnInterpreter()),
        processed_delay_seconds=4,
    )

    cycle = worker.run_once()

    assert cycle.completed == 1
    assert cycle.next_delay_seconds == 4


def test_single_instance_lock_rejects_second_worker(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"

    with SingleInstanceLock(lock_path):
        with pytest.raises(MaintenanceWorkerAlreadyRunningError):
            with SingleInstanceLock(lock_path):
                raise AssertionError("second lock unexpectedly started")


def test_cli_requires_dedicated_analysis_url_for_local_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WINTER_ANALYSIS_LLM_URL", raising=False)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--turn-understanding-path",
                str(tmp_path / "turns.sqlite"),
                "--lock-path",
                str(tmp_path / "maintenance.lock"),
                "--once",
            ]
        )

    assert error.value.code == 2


def test_fake_cli_run_is_explicit_and_offline(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "turns.sqlite")

    result = main(
        [
            "--turn-understanding-path",
            str(tmp_path / "turns.sqlite"),
            "--lock-path",
            str(tmp_path / "maintenance.lock"),
            "--backend",
            "fake",
            "--once",
        ]
    )

    assert result == 0
    assert len(repository.list(status="completed")) == 1
