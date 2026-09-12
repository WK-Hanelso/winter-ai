from dataclasses import dataclass, field

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
from companion.errors import AdapterUnavailableError as AdapterUnavailableError


@dataclass(frozen=True)
class FakeChatModel:
    prefix: str = "fake: "

    def generate(self, request: ChatRequest) -> ChatResult:
        return ChatResult(text=f"{self.prefix}{request.prompt}")


@dataclass(frozen=True)
class FailingChatModel:
    message: str = "fake chat model is unavailable"

    def generate(self, request: ChatRequest) -> ChatResult:
        raise AdapterUnavailableError(self.message)


@dataclass(frozen=True)
class FakeSpeechToText:
    transcript: str = "fake transcript"

    def transcribe(self, audio: AudioInput) -> Transcript:
        return Transcript(text=self.transcript)


@dataclass(frozen=True)
class FakeTextToSpeech:
    media_type: str = "audio/fake"

    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        return AudioOutput(data=request.text.encode("utf-8"), media_type=self.media_type)


@dataclass
class InMemoryConversationRepository:
    _messages: list[ConversationMessage] = field(default_factory=list)

    def append(self, message: ConversationMessage) -> ConversationAppendReceipt:
        self._messages.append(message)
        return ConversationAppendReceipt(id=len(self._messages))

    def list_messages(self) -> tuple[ConversationMessage, ...]:
        return tuple(self._messages)
