from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from companion.contracts import (
    AudioInput,
    AudioOutput,
    ChatRequest,
    ChatResult,
    ConversationMessage,
    SpeechRequest,
    Transcript,
)

if TYPE_CHECKING:
    from companion.beliefs import Belief
    from companion.memory import DirectMemoryStatement, Memory
    from companion.outcome import OutcomeMemoryClaim, ShadowOutcomeEvent
    from companion.turn_understanding import (
        ShadowTurnEvent,
        TurnInterpretationRequest,
    )


class ChatModel(Protocol):
    def generate(self, request: ChatRequest) -> ChatResult: ...


class SpeechToText(Protocol):
    def transcribe(self, audio: AudioInput) -> Transcript: ...


class TextToSpeech(Protocol):
    def synthesize(self, request: SpeechRequest) -> AudioOutput: ...


class ConversationRepository(Protocol):
    def append(self, message: ConversationMessage) -> None: ...

    def list_messages(self) -> Sequence[ConversationMessage]: ...


class MemoryCandidateRepository(Protocol):
    """Port for reviewed candidates and direct user-statement memories."""

    def add_candidate(
        self,
        *,
        kind: str,
        content: str,
        source: str = "user_explicit",
        importance: int = 5,
        confidence: float = 1.0,
    ) -> Memory: ...

    def remember_explicit(self, *, kind: str, content: str) -> Memory: ...

    def remember_direct_statement(
        self, statement: DirectMemoryStatement
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
