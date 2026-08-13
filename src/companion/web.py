"""겨울이 on a phone: hold to talk, hear her answer.

The CLI proved the path works and cannot be used away from the machine that
runs it. This is the same path with a browser in front: 천우 holds a button,
the recording goes to whisper, the answer comes back sentence by sentence as
audio he can hear through his earbuds.

Nothing about the companion is reimplemented here. `CompanionCore` decides what
she says, the two voice servers say it, and this file is the door — it holds no
opinion about her.

Sentence at a time, over one connection
---------------------------------------
The whole answer takes long enough that returning it in one piece is a wall of
silence. The pipeline already yields converted audio a sentence at a time, so
each piece is sent as it is ready, over server-sent events: text first so the
screen fills while she is still speaking, then the audio for that sentence.

Audio rides inside the event as base64. It costs a third more bytes than a
separate fetch per piece, and it saves a round trip per sentence and any
question of what happens when a piece is fetched twice. On a private network
that is the right side of the trade.

One conversation
----------------
There is one person here. The conversation is a single SQLite file rather than
a session per browser tab, so 겨울이 remembers the same thread whether he is on
his phone or at the desk.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

from companion.adapters.http_speech import STAGE_ONE_URLS, HttpSpeechModel
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.seedvc import DEFAULT_SERVER_URL as DEFAULT_VC_URL
from companion.adapters.seedvc import SeedVcVoiceConverter
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.adapters.whisper_cpp import DEFAULT_SERVER_URL as DEFAULT_STT_URL
from companion.adapters.whisper_cpp import WhisperCppSpeechToText
from companion.context import ConversationContextBuilder
from companion.contracts import AudioInput, SpeechRequest
from companion.core import CompanionCore
from companion.dialogue_act import classify as classify_dialogue_act
from companion.identity import IdentityRepositoryError, JsonIdentityRepository
from companion.verbal_style import ALLOWED_PROFILES, VerbalStylePlanner, load_verbal_style
from companion.voice_pipeline import stream
from companion.voice_profile import ProsodyPlanner

# What the handler hands the companion so it can report progress while the
# connection is still open: an event name and a payload of strings.
Emit = Callable[[str, dict[str, str]], None]

DEFAULT_PORT = 8000
DEFAULT_MODEL_URL = "http://127.0.0.1:8080"
DEFAULT_IDENTITY = Path("data") / "identity.json"
DEFAULT_CONVERSATION = Path("data") / "conversation.sqlite"
PAGE = Path(__file__).resolve().parent / "static" / "index.html"


class Companion:
    """One conversation, one turn at a time.

    The lock is not about correctness of the stored conversation — it is that
    two turns at once would share one GPU and both arrive later than if they had
    waited. There is one person here.
    """

    def __init__(
        self,
        core: CompanionCore,
        tts: HttpSpeechModel,
        converter: SeedVcVoiceConverter,
        stt: WhisperCppSpeechToText,
    ) -> None:
        self._core = core
        self._tts = tts
        self._converter = converter
        self._stt = stt
        self._lock = threading.Lock()

    def hear(self, audio: bytes, filename: str) -> str:
        return self._stt.transcribe(AudioInput(data=audio, media_type="audio/webm")).text

    def answer(self, text: str, emit: Emit) -> None:
        """Answer once, emitting each sentence and then its audio."""
        prosody = ProsodyPlanner().plan(classify_dialogue_act(text))
        written: list[str] = []
        pending: list[str] = []
        done = threading.Event()

        def think() -> None:
            try:
                self._core.respond_to_text_streaming(text, pending.append)
            finally:
                done.set()

        def sentences() -> Iterator[str]:
            index = 0
            while True:
                if index < len(pending):
                    sentence = pending[index]
                    index += 1
                    written.append(sentence)
                    emit("sentence", {"text": sentence})
                    yield sentence
                elif done.is_set() and index >= len(pending):
                    return
                else:
                    time.sleep(0.02)

        thinking = threading.Thread(target=think, daemon=True)
        thinking.start()
        started = time.perf_counter()
        first = True
        with self._lock:
            for piece in stream(
                sentences(),
                lambda sentence: self._tts.synthesize(
                    SpeechRequest(
                        text=sentence,
                        emotion=prosody.emotion,
                        pace=1.0,
                        energy=prosody.energy,
                        pitch_offset=prosody.pitch_offset,
                    )
                ),
                self._converter.convert,
            ):
                if first:
                    print(f"  첫 소리까지 {time.perf_counter() - started:.1f}초", flush=True)
                    first = False
                emit("audio", {"wav": base64.b64encode(piece.data).decode()})
        thinking.join()
        emit("done", {"text": " ".join(written)})


def make_handler(companion: Companion) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            if self.path == "/health":
                self._send(200, b"ok", "text/plain")
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            if self.path == "/listen":
                try:
                    text = companion.hear(body, "speech.webm")
                except Exception as error:  # noqa: BLE001 - reported to the caller
                    self._send(
                        400,
                        json.dumps({"error": str(error)}, ensure_ascii=False).encode(),
                        "application/json",
                    )
                    return
                self._send(
                    200,
                    json.dumps({"text": text}, ensure_ascii=False).encode(),
                    "application/json",
                )
                return
            if self.path == "/turn":
                self._turn(body)
                return
            self.send_error(404)

        def _turn(self, body: bytes) -> None:
            try:
                text = str(json.loads(body or b"{}").get("text", "")).strip()
                if not text:
                    raise ValueError("할 말이 비었습니다")
            except Exception as error:  # noqa: BLE001 - reported to the caller
                self._send(
                    400,
                    json.dumps({"error": str(error)}, ensure_ascii=False).encode(),
                    "application/json",
                )
                return
            print(f"천우> {text}", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(event: str, payload: dict[str, str]) -> None:
                data = json.dumps(payload, ensure_ascii=False)
                self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
                self.wfile.flush()

            try:
                companion.answer(text, emit)
            except Exception as error:  # noqa: BLE001 - the stream is already open
                emit("error", {"message": str(error)})

        def log_message(self, format: str, *args: object) -> None:
            """Every request, with who asked.

            Silence here cost an evening: when the phone could not load the
            page, there was no way to tell a request that never arrived from one
            that arrived and failed, and the two need opposite fixes.
            """
            when = datetime.now(UTC).strftime("%H:%M:%S")
            print(f"[{when}] {self.client_address[0]} {format % args}", flush=True)

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="겨울이와 브라우저로 대화합니다.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    # 0.0.0.0 rather than loopback: the point is the phone. What keeps this
    # private is the network it is on, not the interface it binds to.
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104
    parser.add_argument("--model-url", default=DEFAULT_MODEL_URL)
    parser.add_argument("--stage-one", choices=sorted(STAGE_ONE_URLS), default="chatterbox")
    parser.add_argument("--vc-url", default=DEFAULT_VC_URL)
    parser.add_argument("--stt-url", default=DEFAULT_STT_URL)
    parser.add_argument("--identity-path", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--conversation-path", type=Path, default=DEFAULT_CONVERSATION)
    parser.add_argument("--style", choices=ALLOWED_PROFILES, default="reference_conversation")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        identity = JsonIdentityRepository(arguments.identity_path).load()
    except IdentityRepositoryError as error:
        print(f"Identity를 읽지 못했습니다: {error}", file=sys.stderr)
        return 1
    arguments.conversation_path.parent.mkdir(parents=True, exist_ok=True)
    core = CompanionCore(
        LlamaCppHttpChatModel(base_url=arguments.model_url),
        SqliteConversationRepository(arguments.conversation_path),
        ConversationContextBuilder(max_messages=12, max_characters=4000),
        identity=identity,
        verbal_style_planner=VerbalStylePlanner(load_verbal_style(arguments.style)),
    )
    companion = Companion(
        core,
        HttpSpeechModel(base_url=STAGE_ONE_URLS[arguments.stage_one]),
        SeedVcVoiceConverter(base_url=arguments.vc_url),
        WhisperCppSpeechToText(base_url=arguments.stt_url),
    )
    server = ThreadingHTTPServer((arguments.host, arguments.port), make_handler(companion))
    started = datetime.now(UTC).strftime("%H:%M")
    print(f"[{started}] 겨울이 대기 중: http://{arguments.host}:{arguments.port}/", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
