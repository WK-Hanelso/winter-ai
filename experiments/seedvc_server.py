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
DEFAULT_DIFFUSION_STEPS = 30
INFERENCE_CFG_RATE = 0.7
# Stage 2 sets the pace, because stage 1 would not. The Reference speaks at 2.80
# syllables a second, measured over 214 of her own clips; Chatterbox at its
# defaults speaks at 6.45, which 천우 heard immediately as "목소리는 완전
# reference인데 속도가 다르다". Upstream documents cfg_weight as the pacing
# control and it moved the rate by less than a tenth, so the stretch happens
# here. 1.3 is his choice by ear from a sweep — not the value that matches her
# average, which is 2.2 and sounds stretched.
DEFAULT_LENGTH_ADJUST = 1.15


def conversion_arguments(
    checkpoint: Path | None,
    config: Path | None,
    length_adjust: float,
    diffusion_steps: int,
    *,
    f0_condition: bool = False,
    auto_f0_adjust: bool = False,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        checkpoint=str(checkpoint) if checkpoint else None,
        config=str(config) if config else None,
        f0_condition=f0_condition,
        auto_f0_adjust=auto_f0_adjust,
        semi_tone_shift=0,
        diffusion_steps=diffusion_steps,
        length_adjust=length_adjust,
        inference_cfg_rate=INFERENCE_CFG_RATE,
        fp16=True,
        source="",
        target="",
        output="",
    )


class Converter:
    """The model, loaded once, converting one request at a time."""

    def __init__(
        self,
        checkpoint: Path | None,
        config: Path | None,
        reference: Path,
        length_adjust: float,
        diffusion_steps: int = DEFAULT_DIFFUSION_STEPS,
        *,
        f0_condition: bool = False,
        auto_f0_adjust: bool = False,
    ) -> None:
        started = time.perf_counter()
        self._arguments = conversion_arguments(
            checkpoint,
            config,
            length_adjust,
            diffusion_steps,
            f0_condition=f0_condition,
            auto_f0_adjust=auto_f0_adjust,
        )
        loaded = inference.load_models(self._arguments)
        # The one line this server exists for: upstream reloads the models on
        # every call, and this makes the second call reuse the first's.
        inference.load_models = lambda _arguments: loaded
        self._reference = reference
        self._lock = threading.Lock()
        mode = "F0 맞춤" if f0_condition and auto_f0_adjust else "F0" if f0_condition else "일반"
        print(f"변환 준비됨 ({mode}, {time.perf_counter() - started:.1f}초)", flush=True)

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
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--f0-condition", action="store_true")
    parser.add_argument("--auto-f0-adjust", action="store_true")
    parser.add_argument("--length-adjust", type=float, default=DEFAULT_LENGTH_ADJUST)
    # Fewer steps is less time per sentence. Whether it is also less quality is
    # a question for 천우's ears, so it is a setting rather than a constant.
    parser.add_argument("--diffusion-steps", type=int, default=DEFAULT_DIFFUSION_STEPS)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.auto_f0_adjust and not arguments.f0_condition:
        print("--auto-f0-adjust는 --f0-condition과 함께 써야 합니다", file=sys.stderr)
        return 2
    if not arguments.f0_condition and (not arguments.checkpoint or not arguments.config):
        print("일반 모델에는 --checkpoint와 --config가 필요합니다", file=sys.stderr)
        return 2
    if bool(arguments.checkpoint) != bool(arguments.config):
        print("--checkpoint와 --config는 함께 지정해야 합니다", file=sys.stderr)
        return 2
    paths = [arguments.reference]
    paths.extend(path for path in (arguments.checkpoint, arguments.config) if path)
    for path in paths:
        if not path.exists():
            print(f"찾지 못했습니다: {path}", file=sys.stderr)
            return 1
    converter = Converter(
        arguments.checkpoint,
        arguments.config,
        arguments.reference,
        arguments.length_adjust,
        arguments.diffusion_steps,
        f0_condition=arguments.f0_condition,
        auto_f0_adjust=arguments.auto_f0_adjust,
    )
    server = ThreadingHTTPServer(("0.0.0.0", arguments.port), make_handler(converter))
    print(f"대기 중: http://0.0.0.0:{arguments.port}/convert", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
