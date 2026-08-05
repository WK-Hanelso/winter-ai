from typing import Protocol, Sequence

from companion.contracts import (
    AudioInput,
    AudioOutput,
    ChatRequest,
    ChatResult,
    ConversationMessage,
    SpeechRequest,
    Transcript,
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
