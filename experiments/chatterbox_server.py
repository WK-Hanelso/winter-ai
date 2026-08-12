"""Stage 1 candidate: Chatterbox Multilingual V3, loaded once, over HTTP.

Same contract as the other two stage-1 servers — POST a sentence, receive a wav
— so choosing a stage 1 stays a matter of choosing a URL.

Korean is on Chatterbox's official language list and it synthesizes without a
reference prompt. That second part is what makes it worth trying: the prompt is
where CosyVoice's leaked opening comes from, and the trim that removes the leak
is what silently ate a sentence today.

The language is fixed at startup rather than sent per request. A caller that
could pass a language could change what 겨울이 speaks by accident, and the model
takes the same text either way.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import sys
import threading
import time
import wave

from chatterbox.mtl_tts import ChatterboxMultilingualTTS
import torch

DEFAULT_PORT = 8093
LANGUAGE = "ko"
# 800M parameters in float32 is 3.2 GiB, and the card is 6. Qwen needs 2.4 of it
# to answer on the GPU instead of the CPU, which is worth seven seconds a turn —
# more than anything else in the path. Halving is how that room is found.
#
# Split because the two halves fail differently: t3 is a Llama backbone and
# takes half precision the way any transformer does, while s3gen ends in a
# vocoder, where fp16 is likelier to show up as noise. Each can be turned off.
HALF_PRECISION_PARTS = ("t3", "s3gen")


class Voice:
    def __init__(self, device: str, half: Sequence[str] = ()) -> None:
        started = time.perf_counter()
        # The released package takes the device and nothing else. The README on
        # master shows a `t3_model="v3"` argument that 0.1.7 does not have, so
        # this is whatever multilingual checkpoint the release ships.
        self._model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        for name in half:
            part = getattr(self._model, name, None)
            if part is None or not hasattr(part, "half"):
                raise SystemExit(f"반정밀도로 바꿀 수 없는 부분입니다: {name}")
            part.half()
            print(f"  {name}: float16", flush=True)
        if half and torch.cuda.is_available():
            # The float32 weights were already on the card when they were
            # halved, and the caching allocator holds what they freed. Without
            # this the memory shows as still used and the point of halving —
            # making room for the language model — is lost.
            torch.cuda.empty_cache()
        self._lock = threading.Lock()
        print(f"목소리 준비됨 ({device}, {time.perf_counter() - started:.1f}초)", flush=True)

    def speak(self, text: str) -> bytes:
        started = time.perf_counter()
        with self._lock:
            wav = self._model.generate(text, language_id=LANGUAGE)
        samples = (wav.squeeze(0).clamp(-1, 1) * 32767).to(torch.int16).cpu().numpy()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self._model.sr)
            handle.writeframes(samples.tobytes())
        seconds = len(samples) / self._model.sr
        elapsed = time.perf_counter() - started
        print(
            f"  {seconds:.1f}초 생성, {elapsed:.1f}초 걸림 (rtf {elapsed / seconds:.2f})",
            flush=True,
        )
        return buffer.getvalue()


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
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument(
        "--half",
        default=" ".join(HALF_PRECISION_PARTS),
        help="반정밀도로 둘 부분을 공백으로 구분. 빈 문자열이면 전부 float32.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    parts = arguments.half.split()
    unknown = [name for name in parts if name not in HALF_PRECISION_PARTS]
    if unknown:
        print(f"모르는 부분입니다: {', '.join(unknown)}", file=sys.stderr)
        return 1
    voice = Voice(arguments.device, parts)
    server = ThreadingHTTPServer(("0.0.0.0", arguments.port), make_handler(voice))
    print(f"대기 중: http://0.0.0.0:{arguments.port}/speak", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
