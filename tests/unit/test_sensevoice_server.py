from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
from urllib.request import Request, urlopen

import pytest

from companion.adapters.audio_upload import multipart
from companion.sensevoice_server import SenseVoiceEngine, make_handler, multipart_file


def test_multipart_file_returns_only_uploaded_audio() -> None:
    body, content_type = multipart(
        "file", "speech.wav", b"RIFF-private-audio", {"model": "sensevoice"}
    )

    assert multipart_file(content_type, body) == b"RIFF-private-audio"


def test_engine_invokes_local_binary_and_uses_ephemeral_audio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        audio_path = Path(command[-1])
        seen["command"] = command
        seen["audio"] = audio_path.read_bytes()
        return subprocess.CompletedProcess(command, 0, stdout="정확한 문장\n", stderr="")

    monkeypatch.setattr("companion.sensevoice_server.subprocess.run", fake_run)
    engine = SenseVoiceEngine(tmp_path / "sensevoice", tmp_path / "model.gguf")

    assert engine.transcribe(b"RIFF-audio") == "정확한 문장"
    assert seen["audio"] == b"RIFF-audio"
    assert seen["command"][:4] == [
        str(tmp_path / "sensevoice"),
        "-m",
        str(tmp_path / "model.gguf"),
        "-a",
    ]
    assert not Path(seen["command"][-1]).exists()  # type: ignore[index, arg-type]


class FakeTranscriber:
    def __init__(self) -> None:
        self.audio = b""

    def transcribe(self, audio: bytes) -> str:
        self.audio = audio
        return "폰에서 말한 내용"


def test_http_service_exposes_health_and_openai_compatible_transcription() -> None:
    engine = FakeTranscriber()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    body, content_type = multipart("file", "speech.wav", b"RIFF-audio", {})
    request = Request(
        f"{base_url}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urlopen(f"{base_url}/health", timeout=2) as response:
            assert json.loads(response.read()) == {"status": "ok"}
        with urlopen(request, timeout=2) as response:
            assert json.loads(response.read()) == {"text": "폰에서 말한 내용"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert engine.audio == b"RIFF-audio"
