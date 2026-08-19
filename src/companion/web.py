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
import os
from pathlib import Path
import sys
import threading
import time

from companion.adapters.codex_worker import CodexReadOnlyWorker
from companion.adapters.http_speech import STAGE_ONE_URLS, HttpSpeechModel
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.seedvc import DEFAULT_SERVER_URL as DEFAULT_VC_URL
from companion.adapters.seedvc import SeedVcVoiceConverter
from companion.adapters.sensevoice import DEFAULT_SERVER_URL as DEFAULT_SENSEVOICE_URL
from companion.adapters.sensevoice import SenseVoiceSpeechToText
from companion.adapters.sqlite_repository import (
    ConversationRepositoryError,
    SqliteConversationRepository,
)
from companion.adapters.whisper_cpp import DEFAULT_SERVER_URL as DEFAULT_STT_URL
from companion.adapters.whisper_cpp import WhisperCppSpeechToText
from companion.beliefs import (
    ActiveBeliefRetriever,
    BeliefRepositoryError,
    SqliteBeliefRepository,
)
from companion.context import ConversationContextBuilder
from companion.contracts import AudioInput, SpeechRequest
from companion.core import CompanionCore
from companion.day_reflection import (
    ActiveDayReflectionRetriever,
    DayReflectionRepositoryError,
    SqliteDayReflectionRepository,
)
from companion.dialogue_act import classify as classify_dialogue_act
from companion.identity import IdentityRepositoryError, JsonIdentityRepository
from companion.memory import (
    ActiveMemoryRetriever,
    MemoryRepositoryError,
    SqliteMemoryRepository,
)
from companion.open_loops import (
    ActiveOpenLoopRetriever,
    OpenLoopRepositoryError,
    SqliteOpenLoopRepository,
)
from companion.outcome import OutcomeRepositoryError, SqliteOutcomeRepository
from companion.ports import SpeechToText
from companion.turn_understanding import (
    SqliteTurnUnderstandingRepository,
    TurnUnderstandingRepositoryError,
)
from companion.verbal_style import ALLOWED_PROFILES, VerbalStylePlanner, load_verbal_style
from companion.voice_latency import (
    SqliteVoiceLatencyRepository,
    VoiceLatencyError,
    VoiceLatencyRepositoryError,
    VoiceLatencySample,
)
from companion.voice_pipeline import stream
from companion.voice_profile import ProsodyPlanner
from companion.winter_events import (
    SqliteWinterEventRepository,
    SystemClock,
    WinterEventRepositoryError,
)

# What the handler hands the companion so it can report progress while the
# connection is still open: an event name and a payload of strings.
Emit = Callable[[str, dict[str, str | float]], None]

DEFAULT_PORT = 8000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_MAX_REQUEST_BYTES = 16 * 1024 * 1024
DEFAULT_MODEL_URL = os.environ.get("WINTER_LLM_URL", "http://127.0.0.1:18080")
DEFAULT_DATA = Path(os.environ.get("WINTER_DATA_DIR", "data"))
DEFAULT_IDENTITY = DEFAULT_DATA / "identity.json"
DEFAULT_CONVERSATION = DEFAULT_DATA / "conversations.sqlite"
DEFAULT_MEMORIES = DEFAULT_DATA / "memories.sqlite"
DEFAULT_BELIEFS = DEFAULT_DATA / "beliefs.sqlite"
DEFAULT_DIALOGUE_STATE = DEFAULT_DATA / "dialogue_state.sqlite"
DEFAULT_TURN_UNDERSTANDING = DEFAULT_DATA / "turn_understanding.sqlite"
DEFAULT_DAY_REFLECTIONS = DEFAULT_DATA / "day_reflections.sqlite"
DEFAULT_OUTCOMES = DEFAULT_DATA / "outcomes.sqlite"
DEFAULT_WINTER_EVENTS = DEFAULT_DATA / "winter_events.sqlite"
DEFAULT_VOICE_METRICS = DEFAULT_DATA / "voice_latency.sqlite"
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
        converter: SeedVcVoiceConverter | None,
        stt: SpeechToText,
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
        started = time.perf_counter()
        prosody = ProsodyPlanner().plan(classify_dialogue_act(text))
        written: list[str] = []
        pending: list[str] = []
        done = threading.Event()

        def think() -> None:
            try:
                self._core.respond_to_text_streaming(
                    text,
                    pending.append,
                    on_status=lambda message: emit(
                        "status",
                        {
                            "message": message,
                            "server_elapsed_ms": _elapsed_ms(started),
                        },
                    ),
                )
            finally:
                done.set()

        def sentences() -> Iterator[str]:
            index = 0
            while True:
                if index < len(pending):
                    sentence = pending[index]
                    index += 1
                    written.append(sentence)
                    emit(
                        "sentence",
                        {
                            "text": sentence,
                            "server_elapsed_ms": _elapsed_ms(started),
                        },
                    )
                    yield sentence
                elif done.is_set() and index >= len(pending):
                    return
                else:
                    time.sleep(0.02)

        thinking = threading.Thread(target=think, daemon=True)
        thinking.start()
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
                (lambda audio: audio)
                if self._converter is None
                else self._converter.convert,
            ):
                if first:
                    print(f"  첫 소리까지 {time.perf_counter() - started:.1f}초", flush=True)
                    first = False
                emit(
                    "audio",
                    {
                        "wav": base64.b64encode(piece.data).decode(),
                        "server_elapsed_ms": _elapsed_ms(started),
                    },
                )
        thinking.join()
        emit("done", {"text": " ".join(written)})


def make_handler(
    companion: Companion,
    voice_latency_repository: SqliteVoiceLatencyRepository,
    *,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
) -> type[BaseHTTPRequestHandler]:
    if max_request_bytes < 1:
        raise ValueError("max_request_bytes must be positive")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # The page changes during local iteration and JSON responses may
            # contain private conversation data. Do not let a phone reuse an
            # old client or retain these responses in its HTTP cache.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _request_error(self, status: int, message: str) -> None:
            self.close_connection = True
            self._send(
                status,
                json.dumps({"error": message}, ensure_ascii=False).encode(),
                "application/json",
            )

        def _read_request_body(self) -> bytes | None:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self._request_error(411, "Content-Length header is required")
                return None
            try:
                length = int(raw_length)
            except ValueError:
                self._request_error(400, "Content-Length must be an integer")
                return None
            if length < 0:
                self._request_error(400, "Content-Length must not be negative")
                return None
            if length > max_request_bytes:
                self._request_error(413, f"request body exceeds {max_request_bytes} bytes")
                return None
            return self.rfile.read(length) if length else b""

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            if self.path == "/health":
                self._send(200, b"ok", "text/plain")
                return
            if self.path == "/voice-metrics":
                try:
                    summary = voice_latency_repository.summarize().to_dict()
                except VoiceLatencyRepositoryError as error:
                    self._send(
                        500,
                        json.dumps({"error": str(error)}).encode(),
                        "application/json",
                    )
                    return
                self._send(
                    200,
                    json.dumps(summary).encode(),
                    "application/json",
                )
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/listen", "/turn", "/voice-metric"):
                self.send_error(404)
                return
            body = self._read_request_body()
            if body is None:
                return
            if self.path == "/listen":
                started = time.perf_counter()
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
                    json.dumps(
                        {"text": text, "server_stt_ms": _elapsed_ms(started)},
                        ensure_ascii=False,
                    ).encode(),
                    "application/json",
                )
                return
            if self.path == "/turn":
                self._turn(body)
                return
            if self.path == "/voice-metric":
                self._voice_metric(body)
                return

        def _voice_metric(self, body: bytes) -> None:
            try:
                payload = json.loads(body or b"{}")
                if not isinstance(payload, dict):
                    raise VoiceLatencyError("voice latency payload must be an object")
                sample = VoiceLatencySample.from_payload(payload)
                voice_latency_repository.record(sample)
                summary = voice_latency_repository.summarize()
            except (json.JSONDecodeError, VoiceLatencyError) as error:
                self._send(
                    400,
                    json.dumps({"error": str(error)}).encode(),
                    "application/json",
                )
                return
            except VoiceLatencyRepositoryError as error:
                self._send(
                    500,
                    json.dumps({"error": str(error)}).encode(),
                    "application/json",
                )
                return
            print(
                "  Voice 실측: "
                f"{summary.sample_size}/{summary.target_sample_size} turn, "
                f"재생 p50={_seconds(summary.release_to_play_p50_ms)} "
                f"p95={_seconds(summary.release_to_play_p95_ms)}",
                flush=True,
            )
            self._send(
                200,
                json.dumps(summary.to_dict()).encode(),
                "application/json",
            )

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

            def emit(event: str, payload: dict[str, str | float]) -> None:
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


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="겨울이와 브라우저로 대화합니다.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="listen address; keep loopback when Tailscale Serve is the remote ingress",
    )
    parser.add_argument(
        "--max-request-bytes",
        type=_positive_integer,
        default=DEFAULT_MAX_REQUEST_BYTES,
        help="maximum accepted HTTP request body size",
    )
    parser.add_argument("--model-url", default=DEFAULT_MODEL_URL)
    parser.add_argument(
        "--codex-worker",
        action="store_true",
        default=os.environ.get("WINTER_CODEX_WORKER", "").strip().lower()
        in {"1", "true", "yes", "on"},
    )
    parser.add_argument(
        "--codex-executable",
        default=os.environ.get("WINTER_CODEX_BIN", "codex"),
    )
    parser.add_argument(
        "--codex-model",
        default=os.environ.get("WINTER_CODEX_MODEL", "gpt-5.4"),
    )
    parser.add_argument("--stage-one", choices=sorted(STAGE_ONE_URLS), default="chatterbox")
    parser.add_argument("--vc-url", default=DEFAULT_VC_URL)
    parser.add_argument(
        "--no-voice-conversion",
        action="store_true",
        help="stage 1 already carries the selected voice identity",
    )
    parser.add_argument("--stt-backend", choices=("whisper", "sensevoice"), default="whisper")
    parser.add_argument(
        "--stt-url",
        help="STT service URL; defaults to the selected backend's local port",
    )
    parser.add_argument("--identity-path", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--conversation-path", type=Path, default=DEFAULT_CONVERSATION)
    parser.add_argument("--memory-path", type=Path, default=DEFAULT_MEMORIES)
    parser.add_argument("--belief-path", type=Path, default=DEFAULT_BELIEFS)
    parser.add_argument(
        "--dialogue-state-path", type=Path, default=DEFAULT_DIALOGUE_STATE
    )
    parser.add_argument(
        "--turn-understanding-path",
        type=Path,
        default=DEFAULT_TURN_UNDERSTANDING,
    )
    parser.add_argument("--outcome-path", type=Path, default=DEFAULT_OUTCOMES)
    parser.add_argument(
        "--winter-event-path", type=Path, default=DEFAULT_WINTER_EVENTS
    )
    parser.add_argument(
        "--day-reflection-path", type=Path, default=DEFAULT_DAY_REFLECTIONS
    )
    parser.add_argument(
        "--voice-metrics-path",
        type=Path,
        default=DEFAULT_VOICE_METRICS,
    )
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
    try:
        conversation_repository = SqliteConversationRepository(arguments.conversation_path)
    except ConversationRepositoryError as error:
        print(f"대화를 읽지 못했습니다: {error}", file=sys.stderr)
        return 1
    try:
        memory_repository = SqliteMemoryRepository(arguments.memory_path)
        memory_retriever = ActiveMemoryRetriever(memory_repository)
    except MemoryRepositoryError as error:
        print(f"천우에 대한 기억을 읽지 못했습니다: {error}", file=sys.stderr)
        return 1
    try:
        belief_retriever = ActiveBeliefRetriever(
            SqliteBeliefRepository(arguments.belief_path)
        )
    except BeliefRepositoryError as error:
        print(f"겨울이 관점을 읽지 못했습니다: {error}", file=sys.stderr)
        return 1
    try:
        open_loop_repository = SqliteOpenLoopRepository(arguments.dialogue_state_path)
        open_loop_retriever = ActiveOpenLoopRetriever(open_loop_repository)
    except OpenLoopRepositoryError as error:
        print(f"이어갈 이야기를 읽지 못했습니다: {error}", file=sys.stderr)
        return 1
    try:
        turn_understanding_repository = SqliteTurnUnderstandingRepository(
            arguments.turn_understanding_path
        )
    except TurnUnderstandingRepositoryError as error:
        print(f"대화 이해 기록을 열지 못했습니다: {error}", file=sys.stderr)
        return 1
    try:
        outcome_repository = SqliteOutcomeRepository(arguments.outcome_path)
    except OutcomeRepositoryError as error:
        print(f"대화 결과 기록을 열지 못했습니다: {error}", file=sys.stderr)
        return 1
    try:
        winter_event_repository = SqliteWinterEventRepository(
            arguments.winter_event_path
        )
    except WinterEventRepositoryError as error:
        print(f"겨울이의 하루 기록을 열지 못했습니다: {error}", file=sys.stderr)
        return 1
    try:
        day_reflection_retriever = ActiveDayReflectionRetriever(
            SqliteDayReflectionRepository(arguments.day_reflection_path)
        )
    except DayReflectionRepositoryError as error:
        print(f"겨울이의 지난날 회고를 읽지 못했습니다: {error}", file=sys.stderr)
        return 1
    try:
        voice_latency_repository = SqliteVoiceLatencyRepository(
            arguments.voice_metrics_path
        )
    except VoiceLatencyRepositoryError as error:
        print(f"Voice 성능 기록을 열지 못했습니다: {error}", file=sys.stderr)
        return 1
    core = CompanionCore(
        LlamaCppHttpChatModel(base_url=arguments.model_url),
        conversation_repository,
        ConversationContextBuilder(max_messages=12, max_characters=4000),
        identity=identity,
        memory_retriever=memory_retriever,
        memory_repository=memory_repository,
        verbal_style_planner=VerbalStylePlanner(load_verbal_style(arguments.style)),
        belief_retriever=belief_retriever,
        open_loop_repository=open_loop_repository,
        open_loop_retriever=open_loop_retriever,
        turn_understanding_repository=turn_understanding_repository,
        outcome_repository=outcome_repository,
        turn_source="web",
        winter_event_repository=winter_event_repository,
        clock=SystemClock(),
        day_reflection_retriever=day_reflection_retriever,
        read_only_worker=(
            CodexReadOnlyWorker(
                Path.cwd(),
                executable=arguments.codex_executable,
                model=arguments.codex_model,
            )
            if arguments.codex_worker
            else None
        ),
    )
    if arguments.stt_backend == "sensevoice":
        stt: SpeechToText = SenseVoiceSpeechToText(
            base_url=arguments.stt_url or DEFAULT_SENSEVOICE_URL
        )
    else:
        stt = WhisperCppSpeechToText(base_url=arguments.stt_url or DEFAULT_STT_URL)
    companion = Companion(
        core,
        HttpSpeechModel(base_url=STAGE_ONE_URLS[arguments.stage_one]),
        None
        if arguments.no_voice_conversion
        else SeedVcVoiceConverter(base_url=arguments.vc_url),
        stt,
    )
    server = ThreadingHTTPServer(
        (arguments.host, arguments.port),
        make_handler(
            companion,
            voice_latency_repository,
            max_request_bytes=arguments.max_request_bytes,
        ),
    )
    started = datetime.now(UTC).strftime("%H:%M")
    print(f"[{started}] 겨울이 대기 중: http://{arguments.host}:{arguments.port}/", flush=True)
    server.serve_forever()
    return 0


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _seconds(milliseconds: float | None) -> str:
    return "-" if milliseconds is None else f"{milliseconds / 1000:.2f}s"


if __name__ == "__main__":
    raise SystemExit(main())
