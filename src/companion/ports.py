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


class AudioRecorder(Protocol):
    def start(self) -> None: ...

    def stop(self) -> AudioInput: ...


class AudioPlayer(Protocol):
    def play(self, audio: AudioOutput) -> None: ...


class ReadyNotifier(Protocol):
    def notify_ready(self, message: str) -> None: ...


class ConversationRepository(Protocol):
    def append(self, message: ConversationMessage) -> None: ...

    def list_messages(self) -> Sequence[ConversationMessage]: ...
