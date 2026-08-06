"""Local HTTP adapters for the selected Whisper.cpp and MeloTTS services."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import AudioInput, AudioOutput, SpeechRequest, Transcript


@dataclass(frozen=True)
class WhisperCppHttpSpeechToText:
    base_url: str
    timeout_seconds: float = 120.0

    def transcribe(self, audio: AudioInput) -> Transcript:
        boundary = "winter-ai-audio-boundary"
        body = _multipart_audio_body(boundary, audio)
        endpoint = f"{self.base_url.rstrip('/')}/inference"
        raw = _post(
            endpoint,
            body,
            f"multipart/form-data; boundary={boundary}",
            self.timeout_seconds,
            "local Whisper.cpp STT",
        )
        try:
            text = json.loads(raw)["text"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise AdapterUnavailableError("local Whisper.cpp STT returned an invalid response") from error
        if not isinstance(text, str) or not text.strip():
            raise AdapterUnavailableError("local Whisper.cpp STT returned an empty transcript")
        return Transcript(text=text.strip())


@dataclass(frozen=True)
class MeloTtsHttpTextToSpeech:
    base_url: str
    timeout_seconds: float = 120.0

    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        endpoint = f"{self.base_url.rstrip('/')}/synthesize"
        raw = _post(
            endpoint,
            json.dumps({"text": request.text, "emotion": request.emotion}).encode("utf-8"),
            "application/json",
            self.timeout_seconds,
            "local MeloTTS",
        )
        if not raw:
            raise AdapterUnavailableError("local MeloTTS returned empty audio")
        return AudioOutput(data=raw, media_type="audio/wav")


def _multipart_audio_body(boundary: str, audio: AudioInput) -> bytes:
    return b"".join(
        (
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="input.wav"\r\n',
            f"Content-Type: {audio.media_type}\r\n\r\n".encode(),
            audio.data,
            f"\r\n--{boundary}--\r\n".encode(),
        )
    )


def _post(endpoint: str, body: bytes, content_type: str, timeout: float, label: str) -> bytes:
    request = Request(endpoint, data=body, headers={"Content-Type": content_type}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as error:
        raise AdapterUnavailableError(f"{label} returned HTTP {error.code} at {endpoint}") from error
    except URLError as error:
        raise AdapterUnavailableError(f"{label} is unavailable at {endpoint}: {error.reason}") from error
    except TimeoutError as error:
        raise AdapterUnavailableError(f"{label} timed out after {timeout:g}s at {endpoint}") from error
