"""Continuously consume internal TurnUnderstanding work in Shadow mode."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import signal
import sys
from threading import Event
from types import TracebackType
from typing import TextIO

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.turn_interpreter import StructuredChatTurnInterpreter
from companion.maintenance import ConversationMaintenancePolicy, MaintenanceMode
from companion.turn_understanding import (
    FakeTurnInterpreter,
    ProcessingResult,
    ShadowAnalysisService,
    SqliteTurnUnderstandingRepository,
    TurnUnderstandingRepositoryError,
)


class MaintenanceWorkerAlreadyRunningError(RuntimeError):
    """Raised when another worker owns the same local queue lock."""


@dataclass(frozen=True)
class MaintenanceCycle:
    processed: int
    completed: int
    failed: int
    deferred: int
    next_delay_seconds: float


class ShadowMaintenanceWorker:
    """Drain one local Shadow queue without touching response or Memory state."""

    def __init__(
        self,
        service: ShadowAnalysisService,
        *,
        batch_size: int = 5,
        idle_seconds: float = 2.0,
        unavailable_backoff_seconds: float = 5.0,
        max_backoff_seconds: float = 60.0,
    ) -> None:
        if batch_size < 1:
            raise ValueError("maintenance batch size must be at least 1")
        if idle_seconds <= 0 or unavailable_backoff_seconds <= 0:
            raise ValueError("maintenance delays must be positive")
        if max_backoff_seconds < unavailable_backoff_seconds:
            raise ValueError("maximum backoff must not be smaller than initial backoff")
        self._service = service
        self._batch_size = batch_size
        self._idle_seconds = idle_seconds
        self._initial_backoff = unavailable_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._current_backoff = unavailable_backoff_seconds

    def run_once(self) -> MaintenanceCycle:
        result = self._service.process_pending(limit=self._batch_size)
        delay = self._next_delay(result)
        return MaintenanceCycle(
            processed=result.processed,
            completed=result.completed,
            failed=result.failed,
            deferred=result.deferred,
            next_delay_seconds=delay,
        )

    def serve(
        self,
        stop: Event,
        *,
        on_cycle: Callable[[MaintenanceCycle], None] | None = None,
    ) -> None:
        while not stop.is_set():
            cycle = self.run_once()
            if on_cycle is not None:
                on_cycle(cycle)
            if cycle.next_delay_seconds > 0:
                stop.wait(cycle.next_delay_seconds)

    def _next_delay(self, result: ProcessingResult) -> float:
        if result.deferred:
            delay = self._current_backoff
            self._current_backoff = min(
                self._max_backoff,
                self._current_backoff * 2,
            )
            return delay
        self._current_backoff = self._initial_backoff
        return self._idle_seconds if result.processed == 0 else 0.0


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: TextIO | None = None

    def __enter__(self) -> SingleInstanceLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise MaintenanceWorkerAlreadyRunningError(
                f"maintenance worker already owns {self._path}"
            ) from error
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


def build_parser() -> argparse.ArgumentParser:
    data = Path(os.environ.get("WINTER_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--turn-understanding-path",
        type=Path,
        default=data / "turn_understanding.sqlite",
    )
    parser.add_argument("--lock-path", type=Path, default=data / "maintenance.lock")
    parser.add_argument(
        "--analysis-url",
        default=os.environ.get("WINTER_ANALYSIS_LLM_URL"),
        help="dedicated local analysis llama-server; never defaults to conversation LLM",
    )
    parser.add_argument("--backend", choices=("local", "fake"), default="local")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--idle-seconds", type=float, default=2.0)
    parser.add_argument("--backoff-seconds", type=float, default=5.0)
    parser.add_argument("--max-backoff-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.backend == "local" and not arguments.analysis_url:
        parser.error(
            "--analysis-url or WINTER_ANALYSIS_LLM_URL is required; "
            "the conversation LLM is never selected implicitly"
        )
    route = ConversationMaintenancePolicy().route()
    if route.mode is not MaintenanceMode.SHADOW:
        print("maintenance worker refused a non-Shadow route", file=sys.stderr)
        return 1
    try:
        repository = SqliteTurnUnderstandingRepository(
            arguments.turn_understanding_path
        )
        interpreter = (
            FakeTurnInterpreter()
            if arguments.backend == "fake"
            else StructuredChatTurnInterpreter(
                LlamaCppHttpChatModel(arguments.analysis_url)
            )
        )
        worker = ShadowMaintenanceWorker(
            ShadowAnalysisService(repository, interpreter),
            batch_size=arguments.batch_size,
            idle_seconds=arguments.idle_seconds,
            unavailable_backoff_seconds=arguments.backoff_seconds,
            max_backoff_seconds=arguments.max_backoff_seconds,
        )
        with SingleInstanceLock(arguments.lock_path):
            if arguments.once:
                _print_cycle(worker.run_once())
                return 0
            stop = Event()
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            worker.serve(stop, on_cycle=_print_cycle)
    except (
        MaintenanceWorkerAlreadyRunningError,
        TurnUnderstandingRepositoryError,
        ValueError,
    ) as error:
        print(f"maintenance worker unavailable: {error}", file=sys.stderr)
        return 1
    return 0


def _print_cycle(cycle: MaintenanceCycle) -> None:
    if not cycle.processed and not cycle.deferred:
        return
    print(
        "maintenance shadow: "
        f"processed={cycle.processed} completed={cycle.completed} "
        f"failed={cycle.failed} deferred={cycle.deferred} "
        f"next_delay={cycle.next_delay_seconds:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
