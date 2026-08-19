from io import BytesIO
import json
from urllib.error import URLError

import pytest

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.sensevoice import SenseVoiceSpeechToText
from companion.contracts import AudioInput


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self._body = BytesIO(body)

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body.read()


def test_sensevoice_posts_converted_audio_to_local_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeHttpResponse:
        captured["url"] = request.full_url  # type: ignore[attr-defined]
        captured["body"] = request.data  # type: ignore[attr-defined]
        captured["timeout"] = timeout
        return FakeHttpResponse(json.dumps({"text": "겨울아 안녕"}).encode())

    monkeypatch.setattr("companion.adapters.sensevoice.to_wav", lambda _: b"RIFF-wav")
    monkeypatch.setattr("companion.adapters.sensevoice.urlopen", fake_urlopen)

    transcript = SenseVoiceSpeechToText("http://sensevoice:8095").transcribe(
        AudioInput(b"browser-audio", "audio/webm")
    )

    assert transcript.text == "겨울아 안녕"
    assert captured["url"] == "http://sensevoice:8095/v1/audio/transcriptions"
    assert b"RIFF-wav" in captured["body"]  # type: ignore[operator]
    assert b'sensevoice-small-q8' in captured["body"]  # type: ignore[operator]
    assert captured["timeout"] == 30.0


def test_sensevoice_does_not_silently_fall_back_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("companion.adapters.sensevoice.to_wav", lambda _: b"RIFF-wav")

    def fail(*args: object, **kwargs: object) -> object:
        raise URLError("connection refused")

    monkeypatch.setattr("companion.adapters.sensevoice.urlopen", fail)

    with pytest.raises(AdapterUnavailableError, match="SenseVoice.*닿지 못했습니다"):
        SenseVoiceSpeechToText().transcribe(AudioInput(b"audio", "audio/webm"))


def test_sensevoice_rejects_empty_audio_before_conversion() -> None:
    with pytest.raises(ValueError, match="empty audio"):
        SenseVoiceSpeechToText().transcribe(AudioInput(b"", "audio/webm"))
