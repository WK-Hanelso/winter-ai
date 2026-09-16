"""Explicit, auditable delegation contracts for external read-only Workers."""

from __future__ import annotations

from dataclasses import dataclass
import re
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
    """Require a named delegation form and an imperative before data may leave.

    The initial route is deliberately opt-in per turn.  A mention such as
    "Codex가 뭐야?" is normal conversation, while "Codex로 분석해줘" is an
    unambiguous delegation instruction.
    """

    _name = r"(?<!\w)(?:codex|코덱스|코드엑스)"
    _action = (
        r"확인|분석|검토|조사|찾아|비교|진단|리뷰|맡기|맡겨|시키|시켜|"
        r"\b(?:read|review|analy(?:ze|se|sis)?|inspect|investigate)\b"
    )
    _delegation = re.compile(
        _name + r"(?:에게|한테|로|를\s*(?:써서|사용해서|이용해서|통해))\s*.*(?:"
        + _action + r")|" + _name + r"\s*(?:써서|사용해서|이용해서)"
    )
    _request_ending = re.compile(r"(?:줘|줄래|해봐|봐줘|해줘|시켜|맡겨|부탁|해라|해)$")
    _question = re.compile(
        r"뭐야|왜|가능해|있어|할\s*수\s*있|되는\s*거야|인\s*거야|"
        # '실패 이유 조사시켜' names the task; '이유가 뭐야' asks Winter.
        r"이유(?:가|는|를)?\s*(?:뭐|무엇|궁금|알고)"
    )

    def route(self, text: str) -> ReadOnlyWorkerRequest | None:
        normalized = " ".join(text.lower().split())
        normalized = normalized.rstrip(".!?。！？,;:~… ")
        if not self._request_ending.search(normalized) or self._question.search(normalized):
            return None
        if "이유" in normalized and not re.search(self._action, normalized.rsplit("이유", 1)[1]):
            return None
        if not self._delegation.search(normalized):
            return None
        return ReadOnlyWorkerRequest(task=text.strip())
