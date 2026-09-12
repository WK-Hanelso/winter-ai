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


class ExplicitCodexRoutingPolicy:
    """Require an explicit Codex name and a work verb before data may leave.

    The initial route is deliberately opt-in per turn.  A mention such as
    "Codex가 뭐야?" is normal conversation, while "Codex로 분석해줘" is an
    unambiguous delegation instruction.
    """

    _worker_names = ("codex", "코덱스", "코드엑스")
    _action_markers = (
        "확인",
        "분석",
        "검토",
        "조사",
        "찾아",
        "찾아봐",
        "비교",
        "진단",
        "원인",
        "리뷰",
        "맡겨",
        "써서",
        "사용해서",
        "read",
        "review",
        "analy",
        "inspect",
        "investigate",
    )

    def route(self, text: str) -> ReadOnlyWorkerRequest | None:
        normalized = " ".join(text.lower().split())
        if not any(name in normalized for name in self._worker_names):
            return None
        if not any(marker in normalized for marker in self._action_markers):
            return None
        return ReadOnlyWorkerRequest(task=text.strip())
