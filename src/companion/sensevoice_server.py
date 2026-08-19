"""Minimal local HTTP service around the SenseVoice GGUF command-line runtime.

The server intentionally stores neither audio nor transcripts. Each upload is
written to a short-lived file because the upstream binary accepts file paths,
then removed when the request ends.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Protocol

MAX_AUDIO_BYTES = 32 * 1024 * 1024


class Transcriber(Protocol):
    def transcribe(self, audio: bytes) -> str: ...


@dataclass
class SenseVoiceEngine:
    binary: Path
    model: Path
    timeout_seconds: float = 30.0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def transcribe(self, audio: bytes) -> str:
        if not audio:
            raise ValueError("empty audio")
        with tempfile.NamedTemporaryFile(suffix=".wav") as upload:
            upload.write(audio)
            upload.flush()
            with self._lock:
                completed = subprocess.run(
                    [
                        str(self.binary),
                        "-m",
                        str(self.model),
                        "-a",
                        upload.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            raise RuntimeError(detail[-1] if detail else "SenseVoice produced no result")
        return completed.stdout.strip()


def multipart_file(content_type: str, body: bytes) -> bytes:
    """Read the `file` part without retaining any other request content."""
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    if not message.is_multipart():
        raise ValueError("multipart/form-data is required")
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name == "file":
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload
            raise ValueError("file field must contain binary audio")
    raise ValueError("file field is required")


def make_handler(engine: Transcriber) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self.send_error(404)
                return
            self._json(200, {"status": "ok"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/audio/transcriptions":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "invalid Content-Length")
                return
            if length <= 0 or length > MAX_AUDIO_BYTES:
                self.send_error(413, "audio upload is empty or too large")
                return
            try:
                audio = multipart_file(
                    self.headers.get("Content-Type", ""),
                    self.rfile.read(length),
                )
                text = engine.transcribe(audio)
            except ValueError as error:
                self._json(400, {"error": str(error)})
                return
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                self._json(503, {"error": f"local SenseVoice failed: {error}"})
                return
            self._json(200, {"text": text})

        def _json(self, status: int, payload: dict[str, str]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # Keep the default request metadata, never the uploaded audio/text.
            super().log_message(format, *args)

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8095)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.binary.is_file() or not arguments.model.is_file():
        raise SystemExit("SenseVoice binary and model must both exist")
    engine = SenseVoiceEngine(arguments.binary, arguments.model)
    ThreadingHTTPServer((arguments.host, arguments.port), make_handler(engine)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
