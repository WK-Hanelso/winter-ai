"""Keep the voice loaded, and answer synthesis requests over HTTP.

Loading the model costs about 43 seconds. Synthesis on a GPU costs about as long
as the speech itself — measured at 1.13x real time, against 4.7-7.8x on a CPU.
So a container per utterance spends seven eighths of its life getting ready,
and the fix is not to keep starting one.

The interface is deliberately small: POST a sentence, receive a wav. No queueing,
no batching, no streaming. Streaming is the obvious next thing and is not here
yet, because it only helps once generation reliably outruns playback — otherwise
the audio catches up with the generator and stutters.

Sentences are split here rather than by the model's text frontend. That frontend
does two jobs at once — it normalises and it splits — and its normaliser breaks
Korean words apart ("지금은" became "지 금은"), so it is off. With it off nothing
splits either, and a long answer comes out as one unbroken generation whose
pauses land in the wrong places. Splitting on sentence endings restores the
phrasing without restoring the normaliser.

Each piece is trimmed, because the leaked reference opening happens once per
generation and there is now more than one generation per answer.

Standard library only. This project stripped FastAPI and its stack out of the
image because their pins fought each other, and a single-caller local server
does not need them back.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import threading
import time
import wave

sys.path.append("/opt/cosyvoice")
sys.path.append("/src")
sys.path.append("/opt/cosyvoice/third_party/Matcha-TTS")
sys.path.append(str(Path(__file__).resolve().parent))

from cosyvoice.cli.cosyvoice import CosyVoice3  # noqa: E402
import torch  # noqa: E402

from companion.speech_segments import PAUSE_SECONDS, split_sentences  # noqa: E402
from trim_leaked_opening import find_cut, frame_levels  # noqa: E402

MODEL_DIR = "/opt/cosyvoice/pretrained_models/CosyVoice3-0.5B"
SPEAKER_ID = "winter"


class Voice:
    """The model, loaded once, spoken to one request at a time.

    A lock rather than a queue: two synthesis calls at once would compete for
    the same GPU and finish later than if they had waited. There is one caller.
    """

    def __init__(self, flow_checkpoint: Path, speaker: Path, speed: float) -> None:
        started = time.perf_counter()
        self._model = CosyVoice3(MODEL_DIR, load_trt=False, load_vllm=False, fp16=False)
        self._load_flow(flow_checkpoint)
        self._model.frontend.spk2info[SPEAKER_ID] = torch.load(str(speaker), map_location="cpu")
        self._speed = speed
        self._rate = self._model.sample_rate
        self._lock = threading.Lock()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"목소리 준비됨 ({device}, {time.perf_counter() - started:.1f}초)", flush=True)

    def _load_flow(self, checkpoint: Path) -> None:
        state = torch.load(str(checkpoint), map_location="cpu")
        state = state.get("model", state)
        weights = {name: value for name, value in state.items() if torch.is_tensor(value)}
        current = self._model.model.flow.state_dict()
        before = {name: tensor.clone() for name, tensor in current.items()}
        self._model.model.flow.load_state_dict(weights, strict=False)
        after = self._model.model.flow.state_dict()
        changed = sum(
            1 for name, tensor in after.items()
            if name in before and not torch.equal(before[name], tensor)
        )
        if changed == 0:
            # Loading a checkpoint that changes nothing is the failure that
            # looks like success: it would serve the untrained voice happily.
            raise SystemExit("학습된 flow를 불러왔지만 가중치가 하나도 바뀌지 않았습니다.")
        print(f"flow: {changed}/{len(after)} 텐서가 바뀜", flush=True)

    def _synthesize(self, sentence: str) -> torch.Tensor:
        pieces = [
            result["tts_speech"]
            for result in self._model.inference_zero_shot(
                # The sentence alone. The separator belongs in the stored
                # speaker's prompt text, and adding it here too put a boundary
                # marker in the middle of what she was asked to say — which came
                # out as noise and Chinese.
                sentence,
                "",
                "",
                zero_shot_spk_id=SPEAKER_ID,
                stream=False,
                speed=self._speed,
                text_frontend=False,
            )
        ]
        if not pieces:
            raise ValueError(f"합성 결과가 비었습니다: {sentence}")
        return torch.cat(pieces, dim=1)

    def _trim(self, audio: torch.Tensor) -> torch.Tensor:
        """Drop the reference speech the model prepends, if it prepended any."""
        samples = (audio.squeeze(0).clamp(-1, 1) * 32767).to(torch.int16).cpu().tolist()
        levels, frame = frame_levels(samples, self._rate)
        cut = find_cut(levels, frame / self._rate)
        if cut is None:
            return audio
        return audio[:, int(cut * self._rate) :]

    def speak(self, text: str) -> tuple[bytes, float]:
        started = time.perf_counter()
        sentences = split_sentences(text)
        gap = torch.zeros((1, int(PAUSE_SECONDS * self._rate)))
        spoken: list[torch.Tensor] = []
        with self._lock:
            for index, sentence in enumerate(sentences):
                piece = self._trim(self._synthesize(sentence))
                if index:
                    spoken.append(gap)
                spoken.append(piece.cpu())
        audio = torch.cat(spoken, dim=1)
        samples = (audio.squeeze(0).clamp(-1, 1) * 32767).to(torch.int16).cpu().numpy()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self._model.sample_rate)
            handle.writeframes(samples.tobytes())
        seconds = len(samples) / self._model.sample_rate
        elapsed = time.perf_counter() - started
        print(
            f"  문장 {len(sentences)}개 | {seconds:.1f}초 생성, {elapsed:.1f}초 걸림 "
            f"(rtf {elapsed / seconds:.2f})",
            flush=True,
        )
        return buffer.getvalue(), seconds


def make_handler(voice: Voice) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')

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
                audio, _ = voice.speak(text)
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
            # The synthesis line above is the useful log; one request line per
            # utterance on top of it is noise.
            return

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="겨울이 목소리를 상주시켜 HTTP로 제공합니다.")
    parser.add_argument("--flow-checkpoint", type=Path, required=True)
    parser.add_argument("--speaker", type=Path, required=True)
    parser.add_argument("--speed", type=float, default=1.12)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    voice = Voice(arguments.flow_checkpoint, arguments.speaker, arguments.speed)
    server = ThreadingHTTPServer((arguments.host, arguments.port), make_handler(voice))
    print(f"http://{arguments.host}:{arguments.port} 에서 대기 중", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
