from dataclasses import dataclass


@dataclass(frozen=True)
class ChatRequest:
    prompt: str


@dataclass(frozen=True)
class ChatResult:
    text: str


@dataclass(frozen=True)
class AudioInput:
    data: bytes
    media_type: str


@dataclass(frozen=True)
class Transcript:
    text: str


@dataclass(frozen=True)
class SpeechRequest:
    text: str
    emotion: str = "neutral"


@dataclass(frozen=True)
class AudioOutput:
    data: bytes
    media_type: str


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str
