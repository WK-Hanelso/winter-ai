"""Interface-specific renderers for the single Companion-ready event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from companion.contracts import SpeechRequest
from companion.ports import AudioPlayer, TextToSpeech


@dataclass(frozen=True)
class PrintReadyNotifier:
    write: Callable[[str], None] = print

    def notify_ready(self, message: str) -> None:
        self.write(message)


@dataclass(frozen=True)
class SpokenReadyNotifier:
    tts: TextToSpeech
    player: AudioPlayer

    def notify_ready(self, message: str) -> None:
        audio = self.tts.synthesize(SpeechRequest(text=message, emotion="warm"))
        self.player.play(audio)
