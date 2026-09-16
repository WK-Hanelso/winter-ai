"""Explicit, auditable delegation contracts for external read-only Workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class WorkerUnavailableError(RuntimeError):
    """Raised when an explicitly requested Worker did not produce a result."""


class WorkerApprovalDeclined(WorkerUnavailableError):
    """The user did not approve external execution; no provider call was made."""


class WorkerQueued(RuntimeError):
    """A durable approved job was accepted, not completed."""


@dataclass(frozen=True)
class ReadOnlyWorkerRequest:
    task: str
    why: str = ""

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("worker task must not be empty")


@dataclass(frozen=True)
class ReadOnlyWorkerResult:
    answer: str
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    worker: str
    model: str

    def __post_init__(self) -> None:
        if not self.answer.strip():
            raise ValueError("worker answer must not be empty")
        if not self.worker.strip() or not self.model.strip():
            raise ValueError("worker provenance must not be empty")


class ReadOnlyWorker(Protocol):
    def execute(self, request: ReadOnlyWorkerRequest) -> ReadOnlyWorkerResult: ...
