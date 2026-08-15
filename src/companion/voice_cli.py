"""겨울이 speaks: type a line, hear the answer.

The narrowest thing that counts as a working voice companion — keyboard in,
speech out. Speech *in* is not here yet; the microphone path is a separate
piece and this one has to work first.

Runs on the Host, unlike `user_cli`. Synthesis needs `docker run` and playback
needs the Host sound device, so this process drives the model server over HTTP
and the synthesiser over Docker.

The default style profile is `reference_conversation`. The two alternatives are
each wrong in one direction: `base` gives no instruction on ordinary turns, so
the model answers like a general assistant, in paragraphs; `reference_broadcast`
reproduces the measured Reference exactly — one sentence of eight words — and
answers questions badly, replying to a missed presentation with "그래, 좀 힘들어
보여" and dropping the question. `--style` still selects either for comparison.

Length is not fixed by the profile alone. `dialogue_act` decides what kind of
turn this is, and an explicit request for an explanation raises the cap, so
being brief never turns into refusing to answer.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from functools import partial
import io
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave

from companion.adapters.cosyvoice import (
    CosyVoiceSpeechModel,
)
from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.http_speech import STAGE_ONE_URLS, HttpSpeechModel
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.seedvc import DEFAULT_SERVER_URL as DEFAULT_VC_URL
from companion.adapters.seedvc import SeedVcVoiceConverter
from companion.adapters.sqlite_repository import (
    ConversationRepositoryError,
    SqliteConversationRepository,
)
from companion.beliefs import (
    ActiveBeliefRetriever,
    BeliefRepositoryError,
    SqliteBeliefRepository,
)
from companion.context import ConversationContextBuilder
from companion.contracts import AudioOutput, SpeechRequest
from companion.core import CompanionCore
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
from companion.response import CompanionResponse
from companion.speech_segments import PAUSE_SECONDS
from companion.turn_understanding import (
    SqliteTurnUnderstandingRepository,
    TurnUnderstandingRepositoryError,
)
from companion.verbal_style import ALLOWED_PROFILES, VerbalStylePlanner, load_verbal_style
from companion.voice_pipeline import stream
from companion.voice_profile import ProsodyPlanner

DEFAULT_MODEL_URL = os.environ.get("WINTER_LLM_URL", "http://127.0.0.1:18080")
DEFAULT_OUTPUT = Path("generated_audio") / "voice"
# The voice lives in private storage, not in the repository. Both paths come
# from .env because they are Host paths to Reference-derived material.
DEFAULT_FLOW = "derived/training/source-004/exp/flow/epoch_29_whole.pt"
DEFAULT_SPEAKER = "artifacts/voice/winter-speaker.pt"
# Host path: this CLI runs outside the dev container, so the container's
# /workspace/data does not exist here.
DEFAULT_DATA = Path(os.environ.get("WINTER_DATA_DIR", "data"))
DEFAULT_IDENTITY = DEFAULT_DATA / "identity.json"
DEFAULT_CONVERSATION = DEFAULT_DATA / "conversations.sqlite"
DEFAULT_MEMORIES = DEFAULT_DATA / "memories.sqlite"
DEFAULT_BELIEFS = DEFAULT_DATA / "beliefs.sqlite"
DEFAULT_DIALOGUE_STATE = DEFAULT_DATA / "dialogue_state.sqlite"
DEFAULT_TURN_UNDERSTANDING = DEFAULT_DATA / "turn_understanding.sqlite"
DEFAULT_OUTCOMES = DEFAULT_DATA / "outcomes.sqlite"
PROMPT = "천우> "


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="겨울이와 목소리로 대화합니다.")
    parser.add_argument("--model-url", default=DEFAULT_MODEL_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
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
    parser.add_argument("--style", choices=ALLOWED_PROFILES, default="reference_conversation")
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path(os.environ.get("REFERENCE_STORAGE_ROOT", "")),
        help="Reference 자료가 있는 외장 storage. 목소리와 화자 정보가 여기에 있습니다.",
    )
    parser.add_argument("--flow-checkpoint", type=Path)
    parser.add_argument("--speaker", type=Path)
    parser.add_argument(
        "--stage-one",
        choices=sorted(STAGE_ONE_URLS),
        default="chatterbox",
        help=(
            "말을 하는 모델. 목소리는 2단계가 정하므로, 여기서는 한국어를 "
            "또렷하게 읽는지만 봅니다."
        ),
    )
    parser.add_argument("--voice-url", help="1단계 서버 주소. 생략하면 --stage-one의 기본값.")
    parser.add_argument("--vc-url", default=DEFAULT_VC_URL)
    parser.add_argument(
        "--no-voice-conversion",
        action="store_true",
        help="1단계 목소리 그대로 둡니다. 두 단계를 따로 들어볼 때 씁니다.",
    )
    parser.add_argument(
        "--own-container",
        action="store_true",
        help=(
            "목소리 서버 대신 발화마다 컨테이너를 띄웁니다. 서버가 없어도 되지만 "
            "한 문장에 50초쯤 걸립니다."
        ),
    )
    parser.add_argument("--pace", type=float, default=1.12)
    parser.add_argument("--say", help="한 문장만 말하게 하고 끝냅니다.")
    parser.add_argument(
        "--no-play",
        action="store_true",
        help="재생하지 않고 wav 파일만 남깁니다.",
    )
    return parser


def player() -> list[str] | None:
    """Pick a Host player, or None when the machine has no sound output."""
    if shutil.which("paplay"):
        return ["paplay"]
    if shutil.which("aplay"):
        return ["aplay", "-q"]
    return None


def play(path: Path) -> None:
    command = player()
    if command is None:
        print("  (재생 도구가 없어 파일만 남깁니다)", file=sys.stderr)
        return
    environment = dict(os.environ)
    socket = environment.get("PULSE_SOCKET_PATH")
    if socket and "PULSE_SERVER" not in environment:
        environment["PULSE_SERVER"] = f"unix:{socket}"
    subprocess.run([*command, str(path)], env=environment, check=False)


def join_wavs(pieces: Sequence[bytes], path: Path) -> None:
    """Write the pieces as one file, with her pause between them.

    The saved file is what 천우 keeps and re-listens to, so it has to be one
    utterance rather than a directory of fragments. The gap is inserted here for
    the same reason the server inserts it between sentences it says in one go:
    without it the sentences run together.
    """
    if not pieces:
        raise ValueError("빈 소리는 저장할 수 없습니다")
    with wave.open(str(path), "wb") as output:
        for index, piece in enumerate(pieces):
            with wave.open(io.BytesIO(piece)) as source:
                if index == 0:
                    output.setnchannels(source.getnchannels())
                    output.setsampwidth(source.getsampwidth())
                    output.setframerate(source.getframerate())
                    rate = source.getframerate()
                    width = source.getsampwidth() * source.getnchannels()
                else:
                    output.writeframes(b"\x00" * int(PAUSE_SECONDS * rate) * width)
                output.writeframes(source.readframes(source.getnframes()))
    path.chmod(0o600)


def play_bytes(wav: bytes) -> None:
    """Play one piece without leaving it on disk."""
    command = player()
    if command is None:
        return
    environment = dict(os.environ)
    socket = environment.get("PULSE_SOCKET_PATH")
    if socket and "PULSE_SERVER" not in environment:
        environment["PULSE_SERVER"] = f"unix:{socket}"
    subprocess.run(command, input=wav, env=environment, check=False)


def speak(
    core: CompanionCore,
    tts: HttpSpeechModel | CosyVoiceSpeechModel,
    text: str,
    *,
    output_dir: Path,
    pace: float,
    should_play: bool,
    converter: SeedVcVoiceConverter | None = None,
) -> Path | None:
    """One turn: answer, say it, put it in her voice, write, optionally play.

    The conversion is a second step rather than part of synthesis because the
    two stages answer different questions and get replaced separately.
    """
    # The answer is streamed into the pipeline: stage 1 starts on the first
    # sentence while the model is still writing the second, which takes most of
    # its two-and-a-half to four seconds off the wait before anything is said.
    sentences: queue.Queue[str | None] = queue.Queue()
    answer: list[CompanionResponse] = []
    # A failure on the thinking thread would otherwise show up here as an answer
    # with no sentences in it, and the caller would be told the audio was empty
    # rather than that the model could not be reached.
    failure: list[BaseException] = []

    def think() -> None:
        try:
            answer.append(core.respond_to_text_streaming(text, sentences.put))
        except BaseException as error:  # noqa: BLE001 - re-raised on the caller
            failure.append(error)
        finally:
            sentences.put(None)

    thinking = threading.Thread(target=think, daemon=True)
    thinking.start()

    def written() -> Iterator[str]:
        """The first sentence alone, then the rest of the answer together.

        Stage 1 reads better with more text in front of it — sentence by
        sentence, "보며 지내는" came back from the transcript check as "보냬
        지내는" — but waiting for the whole answer is the delay streaming exists
        to remove. The first sentence is what 천우 waits on, so it goes alone
        and everything after it keeps its context.
        """
        rest: list[str] = []
        first = True
        while True:
            sentence = sentences.get()
            if sentence is not None:
                print(f"겨울이> {sentence}")
            if sentence is None:
                if rest:
                    yield " ".join(rest)
                return
            if first:
                first = False
                yield sentence
            else:
                rest.append(sentence)

    # Planned before the answer exists, because synthesis of the first sentence
    # starts before the last one is written. The plan depends on the kind of
    # turn, which is decided from what 천우 said, not from the reply.
    prosody = ProsodyPlanner().plan(classify_dialogue_act(text))

    def say(sentence: str) -> AudioOutput:
        return tts.synthesize(
            SpeechRequest(
                text=sentence,
                emotion=prosody.emotion,
                pace=pace,
                energy=prosody.energy,
                pitch_offset=prosody.pitch_offset,
            )
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"winter-{stamp}.wav"
    # Played as each piece arrives rather than at the end: the point of the
    # pipeline is that the first sound does not wait for the last sentence.
    pieces: list[bytes] = []
    started = time.perf_counter()
    for piece in stream(
        written(),
        say,
        (lambda audio: audio) if converter is None else converter.convert,
    ):
        if not pieces:
            print(f"  첫 소리까지 {time.perf_counter() - started:.1f}초")
        pieces.append(piece.data)
        if should_play:
            play_bytes(piece.data)
    thinking.join()
    if failure:
        raise failure[0]
    join_wavs(pieces, path)
    print(f"  {path}")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        identity = JsonIdentityRepository(arguments.identity_path).load()
    except IdentityRepositoryError as error:
        # Without an identity this answers as a generic assistant rather than as
        # 겨울이, which is the whole point. Refuse instead of quietly degrading.
        print(f"Identity를 읽지 못했습니다: {error}", file=sys.stderr)
        return 1
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
        turn_source="voice",
    )
    tts: HttpSpeechModel | CosyVoiceSpeechModel
    if arguments.own_container:
        flow = arguments.flow_checkpoint or arguments.storage_root / DEFAULT_FLOW
        speaker = arguments.speaker or arguments.storage_root / DEFAULT_SPEAKER
        if not flow.exists() or not speaker.exists():
            # Named rather than left to fail inside a container: without these
            # the voice is not 겨울이's, and that is worth stopping for.
            print(
                "겨울이 목소리를 찾지 못했습니다. REFERENCE_STORAGE_ROOT를 확인하세요.\n"
                f"  flow: {flow}\n  화자: {speaker}",
                file=sys.stderr,
            )
            return 1
        tts = CosyVoiceSpeechModel(
            flow_checkpoint=flow,
            speaker=speaker,
            runner_dir=Path(__file__).resolve().parents[2] / "experiments",
            user=f"{os.getuid()}:{os.getgid()}",
            pace=arguments.pace,
        )
    else:
        tts = HttpSpeechModel(
            base_url=arguments.voice_url or STAGE_ONE_URLS[arguments.stage_one]
        )
    # Bound once rather than splatted from a dict: a dict of mixed value types
    # erases them, and the type checker stops seeing a wrong argument.
    turn = partial(
        speak,
        core,
        tts,
        output_dir=arguments.output_dir,
        pace=arguments.pace,
        should_play=not arguments.no_play,
        converter=(
            None
            if arguments.no_voice_conversion
            else SeedVcVoiceConverter(base_url=arguments.vc_url)
        ),
    )
    if arguments.say:
        try:
            turn(arguments.say)
        except AdapterUnavailableError as error:
            print(f"실패: {error}", file=sys.stderr)
            return 1
        return 0

    print("겨울이와 대화합니다. 끝내려면 빈 줄이나 Ctrl-D.")
    while True:
        try:
            text = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            return 0
        try:
            turn(text)
        except AdapterUnavailableError as error:
            # One failed turn should not end the conversation: the model server
            # or the synthesiser can come back on the next line.
            print(f"  실패: {error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
