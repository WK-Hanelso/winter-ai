"""천우's voice, turned into text by a whisper.cpp server.

The probe that came before this started a container per transcription, which is
right for looking at one recording and wrong for talking: loading the model
costs more than the sentence takes to say. This posts to a server that already
holds it.

Audio arrives from a phone browser as whatever the browser felt like recording —
usually webm/opus, sometimes mp4. whisper-server does not transcode: it wants
16 kHz mono PCM and answers anything else with a 400, which is exactly how the
first recording from the phone failed. So it is converted here, on the way in.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import mimetypes
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import AudioInput, Transcript

DEFAULT_SERVER_URL = "http://127.0.0.1:8094"
# What whisper.cpp reads. Mono because a phone's two channels carry the same
# voice, and 16 kHz because that is what the model was trained on — sending more
# is discarded inside it.
SAMPLE_RATE = 16000


def to_wav(audio: bytes) -> bytes:
    """Whatever the browser recorded, as PCM whisper.cpp can read."""
    try:
        completed = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1",
             "-ar", str(SAMPLE_RATE), "-f", "wav", "pipe:1"],
            input=audio, capture_output=True, timeout=60, check=False,
        )
    except FileNotFoundError as error:
        raise AdapterUnavailableError("ffmpeg가 없어 녹음을 변환할 수 없습니다") from error
    if completed.returncode != 0 or not completed.stdout:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AdapterUnavailableError(
            f"녹음을 변환하지 못했습니다: {detail[-1] if detail else '출력 없음'}"
        )
    return completed.stdout


def _multipart(field: str, filename: str, data: bytes, extra: dict[str, str]) -> tuple[bytes, str]:
    """One file and some fields, encoded by hand.

    Standard library only, like the rest of this project's HTTP: pulling in a
    client library to build a form of two fields is not a trade worth making.
    """
    boundary = f"----winter{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts: list[bytes] = []
    for name, value in extra.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
        f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
    )
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


@dataclass(frozen=True)
class WhisperCppSpeechToText:
    base_url: str = DEFAULT_SERVER_URL
    timeout_seconds: float = 120.0
    filename: str = "speech.wav"

    def transcribe(self, audio: AudioInput) -> Transcript:
        if not audio.data:
            raise ValueError("cannot transcribe empty audio")
        body, content_type = _multipart(
            "file", self.filename, to_wav(audio.data), {"response_format": "json"}
        )
        http_request = Request(
            f"{self.base_url.rstrip('/')}/inference",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read() or b"{}")
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise AdapterUnavailableError(f"받아쓰기 서버가 거절했습니다: {detail}") from error
        except URLError as error:
            raise AdapterUnavailableError(
                f"받아쓰기 서버에 닿지 못했습니다 ({self.base_url}): {error.reason}"
            ) from error
        except TimeoutError as error:
            raise AdapterUnavailableError(
                f"받아쓰기 서버가 {self.timeout_seconds:g}초 안에 답하지 않았습니다"
            ) from error
        return Transcript(text=str(payload.get("text", "")).strip())
