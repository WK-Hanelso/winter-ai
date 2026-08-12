"""Keep the voice-conversion model loaded, and convert over HTTP.

Stage 2 turns whatever the TTS said into 겨울이's voice. Conversion itself is
fast — about 0.4x real time on this GPU — but loading the model costs most of a
minute, so a container per utterance spends nearly all of its life getting
ready. The same reasoning as the synthesis server, and the same shape: POST a
wav, receive a wav.

Upstream's inference is used as it is
------------------------------------
``inference.main`` holds the chunking, the crossfade at chunk boundaries and the
prompt handling. Copying that here would mean maintaining a fork of it, and the
copy would drift. So the models are loaded once and ``inference.load_models`` is
replaced with a function returning them, which is the only reason it reloaded.
Everything else runs upstream's code path unchanged.

The reference is fixed at startup rather than sent per request. It is the one
thing that decides whose voice comes out, and a caller should not be able to
change it by accident.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import types

sys.path.append("/opt/seed-vc")

import inference  # noqa: E402

DEFAULT_PORT = 8091
DIFFUSION_STEPS = 30
# Upstream's defaults, named here so the server's behaviour is visible without
# reading argparse in another file.
LENGTH_ADJUST = 1.0
INFERENCE_CFG_RATE = 0.7


def conversion_arguments(checkpoint: Path, config: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        checkpoint=str(checkpoint),
        config=str(config),
        f0_condition=False,
        auto_f0_adjust=False,
        semi_tone_shift=0,
        diffusion_steps=DIFFUSION_STEPS,
        length_adjust=LENGTH_ADJUST,
        inference_cfg_rate=INFERENCE_CFG_RATE,
        fp16=True,
        source="",
        target="",
        output="",
    )


class Converter:
    """The model, loaded once, converting one request at a time."""

    def __init__(self, checkpoint: Path, config: Path, reference: Path) -> None:
        started = time.perf_counter()
        self._arguments = conversion_arguments(checkpoint, config)
        loaded = inference.load_models(self._arguments)
        # The one line this server exists for: upstream reloads the models on
        # every call, and this makes the second call reuse the first's.
        inference.load_models = lambda _arguments: loaded
        self._reference = reference
        self._lock = threading.Lock()
        print(f"변환 준비됨 ({time.perf_counter() - started:.1f}초)", flush=True)

    def convert(self, wav: bytes) -> bytes:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="winter-vc-") as staging:
            source = Path(staging) / "source.wav"
            source.write_bytes(wav)
            output = Path(staging) / "out"
            self._arguments.source = str(source)
            self._arguments.target = str(self._reference)
            self._arguments.output = str(output)
            with self._lock:
                inference.main(self._arguments)
            produced = sorted(output.glob("*.wav"))
            if not produced:
                raise RuntimeError("변환 결과가 없습니다")
            converted = produced[0].read_bytes()
        print(f"  변환 {time.perf_counter() - started:.1f}초", flush=True)
        return converted


def make_handler(converter: Converter) -> type[BaseHTTPRequestHandler]:
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
            if self.path != "/convert":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                if not length:
                    raise ValueError("오디오가 비었습니다")
                converted = converter.convert(self.rfile.read(length))
            except Exception as error:  # noqa: BLE001 - reported to the caller
                message = str(error).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(converted)))
            self.end_headers()
            self.wfile.write(converted)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    for path in (arguments.checkpoint, arguments.config, arguments.reference):
        if not path.exists():
            print(f"찾지 못했습니다: {path}", file=sys.stderr)
            return 1
    converter = Converter(arguments.checkpoint, arguments.config, arguments.reference)
    server = ThreadingHTTPServer(("0.0.0.0", arguments.port), make_handler(converter))
    print(f"대기 중: http://0.0.0.0:{arguments.port}/convert", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
