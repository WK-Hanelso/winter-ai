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
# V3 rather than whatever the release ships. The earlier checkpoint invented
# speech after the sentence — the same line came back at 1.6, 3.3 and 4.3
# seconds — and V3's notes claim that is reduced.
MODEL_VERSION = "v3"
# 800M parameters in float32 is 3.2 GiB, and the card is 6. Qwen needs 2.4 of it
# to answer on the GPU instead of the CPU, which is worth seven seconds a turn —
# more than anything else in the path. Halving is how that room is found.
#
# Split because the two halves fail differently, and measurement bore that out:
# t3 takes half precision the way any transformer does, while s3gen does not
# take it at all — its vocoder builds a sine source in float32 inside forward
# (hifigan.py:279), so the mismatch is created below the module and cannot be
# fixed from here. That is also the place fp16 would most likely be heard as
# noise, so it is left alone rather than forced.
#
# And the gain is memory, not speed: 0.95 GiB saved, 1.10x faster. A thousand
# sampling steps of this size are held up by launch overhead more than by how
# many bytes each weight takes.
HALF_PRECISION_PARTS = ("t3",)
# What 천우 chose by ear lives here rather than in the request, and these are the
# values the sample he approved was made with. The server had been running
# upstream's defaults (0.5) while the sample came from 0.3, which is a way of
# shipping something other than what was signed off.
DEFAULT_CFG_WEIGHT = 0.3
DEFAULT_EXAGGERATION = 0.5


def _halve_conditioning(model: object) -> list[str]:
    """Put the conditioning tensors in float16, and report which moved.

    Only the floating ones: lengths and token ids are integers, and halving
    those would be meaningless at best. Reported rather than silent because a
    conditioning tensor that stays float32 does not fail here — it fails inside
    generate, a long way from the cause.
    """
    changed: list[str] = []
    conditioning = getattr(model, "conds", None)
    if conditioning is None:
        return changed
    for holder_name in ("t3", "gen"):
        holder = getattr(conditioning, holder_name, None)
        if holder is None:
            continue
        for field in dir(holder):
            if field.startswith("_"):
                continue
            value = getattr(holder, field, None)
            if isinstance(value, torch.Tensor) and value.is_floating_point():
                setattr(holder, field, value.half())
                changed.append(f"{holder_name}.{field}")
    return changed


class Voice:
    def __init__(
        self,
        device: str,
        half: Sequence[str] = (),
        cfg_weight: float = DEFAULT_CFG_WEIGHT,
        exaggeration: float = DEFAULT_EXAGGERATION,
    ) -> None:
        started = time.perf_counter()
        self._model = ChatterboxMultilingualTTS.from_pretrained(
            device=device, t3_model=MODEL_VERSION
        )
        for name in half:
            part = getattr(self._model, name, None)
            if part is None or not hasattr(part, "half"):
                raise SystemExit(f"반정밀도로 바꿀 수 없는 부분입니다: {name}")
            part.half()
            print(f"  {name}: float16", flush=True)
        if half:
            # Halving the module alone is not enough and fails four calls later
            # with "mat1 and mat2 must have the same dtype". The conditioning —
            # the speaker embedding and the prompt features, which come from the
            # checkpoint rather than from a module — stays float32, and a
            # float32 input meeting a float16 weight is what raises. A weight
            # and what it multiplies have to agree.
            changed = _halve_conditioning(self._model)
            print(f"  조건 텐서: {', '.join(changed)}", flush=True)
        if half and torch.cuda.is_available():
            # The float32 weights were already on the card when they were
            # halved, and the caching allocator holds what they freed. Without
            # this the memory shows as still used and the point of halving —
            # making room for the language model — is lost.
            torch.cuda.empty_cache()
        self._cfg_weight = cfg_weight
        self._exaggeration = exaggeration
        self._lock = threading.Lock()
        print(
            f"목소리 준비됨 ({device}, {time.perf_counter() - started:.1f}초, "
            f"cfg {cfg_weight:g}, exaggeration {exaggeration:g})",
            flush=True,
        )

    def speak(self, text: str) -> bytes:
        started = time.perf_counter()
        with self._lock:
            wav = self._model.generate(
                text,
                language_id=LANGUAGE,
                cfg_weight=self._cfg_weight,
                exaggeration=self._exaggeration,
            )
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
    parser.add_argument("--cfg-weight", type=float, default=DEFAULT_CFG_WEIGHT)
    parser.add_argument("--exaggeration", type=float, default=DEFAULT_EXAGGERATION)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    parts = arguments.half.split()
    unknown = [name for name in parts if name not in HALF_PRECISION_PARTS]
    if unknown:
        print(f"모르는 부분입니다: {', '.join(unknown)}", file=sys.stderr)
        return 1
    voice = Voice(arguments.device, parts, arguments.cfg_weight, arguments.exaggeration)
    server = ThreadingHTTPServer(("0.0.0.0", arguments.port), make_handler(voice))
    print(f"대기 중: http://0.0.0.0:{arguments.port}/speak", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
