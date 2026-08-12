"""Keep the Korean voice loaded, and answer synthesis requests over HTTP.

Stage 1 is not supposed to sound like 겨울이. It is supposed to say Korean
clearly; stage 2 makes it her. Reading that the right way around removes most of
what made the voice path fragile — the previous stage 1 was asked to do both
jobs at once, and its zero-shot prompting is what prepended reference speech to
every answer and forced a trim that eventually ate real words.

MeloTTS takes text and returns speech. There is no prompt, so nothing leaks;
there is nothing to trim; whole answers can be synthesized in one call. It also
runs on the CPU, which hands the whole 6 GiB card to the conversion stage.

One container per request cost 54.8 seconds, nearly all of it loading. Same
shape as the other two servers: load once, answer over HTTP, one at a time.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

from melo.api import TTS

DEFAULT_PORT = 8092
LANGUAGE = "KR"


class Voice:
    def __init__(self, speed: float) -> None:
        started = time.perf_counter()
        self._model = TTS(language=LANGUAGE, device="cpu")
        self._speaker = self._model.hps.data.spk2id[LANGUAGE]
        self._speed = speed
        self._lock = threading.Lock()
        print(f"목소리 준비됨 (cpu, {time.perf_counter() - started:.1f}초)", flush=True)

    def speak(self, text: str) -> bytes:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="winter-melo-") as staging:
            path = Path(staging) / "speech.wav"
            with self._lock:
                # Writes through a file because that is the only output this API
                # offers; the directory is private per request so two callers
                # cannot collide.
                self._model.tts_to_file(text, self._speaker, str(path), speed=self._speed)
            audio = path.read_bytes()
        print(f"  합성 {time.perf_counter() - started:.1f}초", flush=True)
        return audio


def make_handler(voice: Voice) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/speak":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                text = str(payload.get("text", "")).strip()
                if not text:
                    raise ValueError("text가 비었습니다")
                audio = voice.speak(text)
            except Exception as error:  # noqa: BLE001 - reported to the caller
                message = json.dumps({"error": str(error)}, ensure_ascii=False).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    voice = Voice(arguments.speed)
    server = ThreadingHTTPServer(("0.0.0.0", arguments.port), make_handler(voice))
    print(f"대기 중: http://0.0.0.0:{arguments.port}/speak", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
