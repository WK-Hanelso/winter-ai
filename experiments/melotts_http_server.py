"""Minimal local HTTP service for the pinned MeloTTS image."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import tempfile

from melo.api import TTS


class Handler(BaseHTTPRequestHandler):
    tts: TTS

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/synthesize":
            self.send_error(404)
            return
        try:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            text = payload["text"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("text is required")
            with tempfile.NamedTemporaryFile(suffix=".wav") as output:
                self.tts.tts_to_file(text, self.tts.hps.data.spk2id["KR"], output.name, speed=1.0)
                audio = Path(output.name).read_bytes()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_error(400, str(error))
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        body = b'{"status":"ready"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    Handler.tts = TTS(language="KR", device="cpu")
    print("MeloTTS ready on http://0.0.0.0:8082", flush=True)
    HTTPServer(("0.0.0.0", 8082), Handler).serve_forever()


if __name__ == "__main__":
    main()
