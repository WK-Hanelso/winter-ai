from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from companion.contracts import (
    AudioInput,
    AudioOutput,
    ChatRequest,
    ChatResult,
    ConversationAppendReceipt,
    ConversationMessage,
    SpeechRequest,
    Transcript,
)

if TYPE_CHECKING:
    from companion.beliefs import Belief
    from companion.memory import DirectMemoryStatement, Memory
    from companion.outcome import OutcomeMemoryClaim, ShadowOutcomeEvent
    from companion.owned_work import OwnedWorkReceipt, OwnedWorkUpdate
    from companion.turn_understanding import (
        ShadowTurnEvent,
        TurnInterpretationRequest,
    )
    from companion.web_search import WebSearchResult
    from companion.winter_events import EventValue, WinterEvent
    from companion.work_orchestration import (
        EvidenceReport,
        IntentBrief,
        WinterDecision,
        WorkerTaskPacket,
        WorkRequest,
    )
    from companion.worker import ReadOnlyWorkerRequest, ReadOnlyWorkerResult
    from companion.worker_gateway import (
        GatewayAuditEntry,
        GatewayEvidence,
        GatewayToolResult,
        GatewayWorkerResult,
        ProjectToolCall,
    )


class ChatModel(Protocol):
    def generate(self, request: ChatRequest) -> ChatResult: ...


class WebSearchPort(Protocol):
    def search(self, query: str) -> WebSearchResult: ...


class ReadOnlyWorker(Protocol):
    def execute(self, request: ReadOnlyWorkerRequest) -> ReadOnlyWorkerResult: ...


class ProjectToolGateway(Protocol):
    """Provider-independent tool boundary for read-only project inspection."""

    @property
    def audit_log(self) -> tuple[GatewayAuditEntry, ...]: ...

    @property
    def evidence_catalog(self) -> tuple[GatewayEvidence, ...]: ...

    def execute(self, call: ProjectToolCall) -> GatewayToolResult: ...


class EvidenceWorker(Protocol):
    """A specialist that can only return a gateway-validated evidence report."""

    def execute(self, packet: WorkerTaskPacket) -> GatewayWorkerResult: ...


class OwnedWorkExecutor(Protocol):
    """A bounded Winter-owned task that returns a reviewed user-facing result."""

    def execute(self, request: WorkRequest) -> ReadOnlyWorkerResult: ...


class OwnedWorkSubmitter(Protocol):
    """Accept a bounded task without waiting for specialist completion."""

    def submit(self, request: WorkRequest) -> OwnedWorkReceipt: ...


class OwnedWorkInbox(Protocol):
    """Expose completed Winter-owned work until the interface acknowledges it."""

    def ready(self) -> tuple[OwnedWorkUpdate, ...]: ...

    def acknowledge(self, update_id: str) -> None: ...


class OwnedWorkJobRepository(OwnedWorkInbox, Protocol):
    """Durable queue boundary used by the non-blocking coordinator."""

    def enqueue(self, work_id: str, request: WorkRequest) -> None: ...

    def claim_pending(self) -> tuple[str, WorkRequest] | None: ...

    def complete(self, update: OwnedWorkUpdate) -> None: ...


class WinterExecutive(Protocol):
    """The single Core Model authority for work intent and final judgment."""

    def create_brief(self, request: WorkRequest) -> IntentBrief: ...

    def review(
        self, brief: IntentBrief, report: EvidenceReport
    ) -> WinterDecision: ...


class SpeechToText(Protocol):
    def transcribe(self, audio: AudioInput) -> Transcript: ...


class TextToSpeech(Protocol):
    def synthesize(self, request: SpeechRequest) -> AudioOutput: ...


@runtime_checkable
class IdempotentConversationRepository(Protocol):
    def append_once(
        self, key: str, message: ConversationMessage,
    ) -> ConversationAppendReceipt: ...


class ConversationRepository(Protocol):
    def append(self, message: ConversationMessage) -> ConversationAppendReceipt: ...

    def list_messages(self) -> Sequence[ConversationMessage]: ...


@runtime_checkable
class WindowedConversationRepository(Protocol):
    """Optional bounded foreground reads; full history remains available for export/audit."""

    def recent_messages(self, limit: int) -> tuple[ConversationMessage, ...]: ...

    def search_messages(
        self, terms: tuple[str, ...], *, exclude_latest: int, limit: int,
    ) -> tuple[ConversationMessage, ...]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class WinterEventRepository(Protocol):
    """Append-only evidence for Winter's actual lived continuity."""

    def record(
        self,
        *,
        event_key: str,
        kind: str,
        subject: str,
        source: str,
        occurred_at: datetime,
        source_ref: str | None = None,
        metadata: Mapping[str, EventValue] | None = None,
    ) -> WinterEvent: ...


class MemoryCandidateRepository(Protocol):
    """Port for reviewed candidates and direct user-statement memories."""

    def list_active(self) -> tuple[Memory, ...]: ...

    def forget_explicit(self, content: str) -> Memory | None: ...

    def add_candidate(
        self,
        *,
        kind: str,
        content: str,
        source: str = "user_explicit",
        importance: int = 5,
        confidence: float = 1.0,
        evidence_ref: str | None = None,
        temporal_scope: str = "unknown",
    ) -> Memory: ...

    def remember_explicit(
        self,
        *,
        kind: str,
        content: str,
        evidence_ref: str | None = None,
    ) -> Memory: ...

    def remember_direct_statement(
        self,
        statement: DirectMemoryStatement,
        *,
        evidence_ref: str,
        temporal_scope: str,
    ) -> Memory | None: ...


class BeliefRetriever(Protocol):
    """Selects reviewed Winter judgements relevant to the current turn."""

    def retrieve(self, query: str) -> tuple[Belief, ...]: ...


class TurnUnderstandingRepository(Protocol):
    """Append-only observation boundary used by CompanionCore Shadow Mode."""

    def enqueue(
        self, request: TurnInterpretationRequest, *, source: str
    ) -> ShadowTurnEvent: ...


class OutcomeRepository(Protocol):
    """Non-operative observer for an actual response and its next user turn."""

    def observe_turn(
        self,
        turn_id: str,
        *,
        source: str,
        user_text: str,
        memory_claims: tuple[OutcomeMemoryClaim, ...] = (),
    ) -> ShadowOutcomeEvent | None: ...

    def record_response(self, turn_id: str, response_text: str) -> None: ...
