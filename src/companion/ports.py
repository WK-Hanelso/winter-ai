from typing import TYPE_CHECKING, Protocol, Sequence

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
    from companion.memory import Memory


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
    """Port for explicitly requested memory candidates."""

    def add_candidate(
        self,
        *,
        kind: str,
        content: str,
        source: str = "user_explicit",
        importance: int = 5,
        confidence: float = 1.0,
    ) -> "Memory": ...
