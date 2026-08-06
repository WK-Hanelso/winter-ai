import json
from io import BytesIO

import pytest

from companion.adapters.voice_http import MeloTtsHttpTextToSpeech, WhisperCppHttpSpeechToText
from companion.contracts import AudioInput, SpeechRequest


class Response:
    def __init__(self, body: bytes) -> None:
        self._body = BytesIO(body)

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body.read()


def test_whisper_adapter_posts_audio_multipart(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> Response:
        captured["body"] = request.data  # type: ignore[attr-defined]
        return Response('{"text": " 전사 결과 "}'.encode("utf-8"))

    monkeypatch.setattr("companion.adapters.voice_http.urlopen", fake_urlopen)
    result = WhisperCppHttpSpeechToText("http://stt:8081").transcribe(AudioInput(b"wav", "audio/wav"))

    assert result.text == "전사 결과"
    assert b"Content-Type: audio/wav" in captured["body"]


def test_melotts_adapter_posts_text_and_returns_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> Response:
        captured["body"] = request.data  # type: ignore[attr-defined]
        return Response(b"RIFFfakewav")

    monkeypatch.setattr("companion.adapters.voice_http.urlopen", fake_urlopen)
    output = MeloTtsHttpTextToSpeech("http://tts:8082").synthesize(SpeechRequest(text="안녕"))

    assert json.loads(captured["body"]) == {"text": "안녕", "emotion": "neutral"}
    assert output.media_type == "audio/wav"
