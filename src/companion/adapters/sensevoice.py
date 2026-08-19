"""SenseVoiceSmall GGUF adapter for the local OpenAI-compatible service."""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.adapters.audio_upload import multipart, to_wav
from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import AudioInput, Transcript

DEFAULT_SERVER_URL = "http://127.0.0.1:8095"


@dataclass(frozen=True)
class SenseVoiceSpeechToText:
    base_url: str = DEFAULT_SERVER_URL
    timeout_seconds: float = 30.0
    filename: str = "speech.wav"

    def transcribe(self, audio: AudioInput) -> Transcript:
        if not audio.data:
            raise ValueError("cannot transcribe empty audio")
        body, content_type = multipart(
            "file",
            self.filename,
            to_wav(audio.data),
            {"model": "sensevoice-small-q8"},
        )
        request = Request(
            f"{self.base_url.rstrip('/')}/v1/audio/transcriptions",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read() or b"{}")
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise AdapterUnavailableError(
                f"SenseVoice 받아쓰기 서버가 거절했습니다: {detail}"
            ) from error
        except URLError as error:
            raise AdapterUnavailableError(
                f"SenseVoice 받아쓰기 서버에 닿지 못했습니다 ({self.base_url}): "
                f"{error.reason}"
            ) from error
        except TimeoutError as error:
            raise AdapterUnavailableError(
                f"SenseVoice 받아쓰기 서버가 {self.timeout_seconds:g}초 안에 답하지 않았습니다"
            ) from error
        return Transcript(text=str(payload.get("text", "")).strip())
