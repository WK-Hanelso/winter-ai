"""Shared browser-audio conversion and multipart upload helpers."""

from __future__ import annotations

import mimetypes
import subprocess
import uuid

from companion.adapters.fake import AdapterUnavailableError

SAMPLE_RATE = 16000


def to_wav(audio: bytes) -> bytes:
    """Convert a browser recording to 16 kHz mono PCM WAV for local ASR."""
    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                "pipe:0",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "wav",
                "pipe:1",
            ],
            input=audio,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as error:
        raise AdapterUnavailableError("ffmpeg가 없어 녹음을 변환할 수 없습니다") from error
    if completed.returncode != 0 or not completed.stdout:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AdapterUnavailableError(
            f"녹음을 변환하지 못했습니다: {detail[-1] if detail else '출력 없음'}"
        )
    return completed.stdout


def multipart(
    field: str,
    filename: str,
    data: bytes,
    extra: dict[str, str],
) -> tuple[bytes, str]:
    """Encode one file and a few text fields without an HTTP dependency."""
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
