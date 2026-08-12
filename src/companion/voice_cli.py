"""겨울이 speaks: type a line, hear the answer.

The narrowest thing that counts as a working voice companion — keyboard in,
speech out. Speech *in* is not here yet; the microphone path is a separate
piece and this one has to work first.

Runs on the Host, unlike `user_cli`. Synthesis needs `docker run` and playback
needs the Host sound device, so this process drives the model server over HTTP
and the synthesiser over Docker.

The default style profile is `base`, not the measured Reference profile. The
Reference profile caps an answer at one sentence of eight words, which is a
faithful reproduction of how the Reference speaks and a poor way to answer a
question: asked what to do about a missed presentation it replied "그래, 좀
힘들어 보여" and dropped the question. Getting the companion running comes
first; `--style` switches profiles once that is no longer the trade.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import partial
import os
from pathlib import Path
import shutil
import subprocess
import sys

from companion.adapters.cosyvoice import CosyVoiceSpeechModel
from companion.adapters.fake import AdapterUnavailableError, InMemoryConversationRepository
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.context import ConversationContextBuilder
from companion.contracts import SpeechRequest
from companion.core import CompanionCore
from companion.identity import IdentityRepositoryError, JsonIdentityRepository
from companion.verbal_style import ALLOWED_PROFILES, VerbalStylePlanner, load_verbal_style

DEFAULT_MODEL_URL = "http://127.0.0.1:8080"
DEFAULT_OUTPUT = Path("generated_audio") / "voice"
# The voice lives in private storage, not in the repository. Both paths come
# from .env because they are Host paths to Reference-derived material.
DEFAULT_FLOW = "derived/training/source-004/exp/flow/epoch_29_whole.pt"
DEFAULT_SPEAKER = "artifacts/voice/winter-speaker.pt"
# Host path: this CLI runs outside the dev container, so the container's
# /workspace/data does not exist here.
DEFAULT_IDENTITY = Path("data") / "identity.json"
PROMPT = "천우> "


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="겨울이와 목소리로 대화합니다.")
    parser.add_argument("--model-url", default=DEFAULT_MODEL_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--identity-path", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--style", choices=ALLOWED_PROFILES, default="base")
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path(os.environ.get("REFERENCE_STORAGE_ROOT", "")),
        help="Reference 자료가 있는 외장 storage. 목소리와 화자 정보가 여기에 있습니다.",
    )
    parser.add_argument("--flow-checkpoint", type=Path)
    parser.add_argument("--speaker", type=Path)
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


def speak(
    core: CompanionCore,
    tts: CosyVoiceSpeechModel,
    text: str,
    *,
    output_dir: Path,
    pace: float,
    should_play: bool,
) -> Path | None:
    """One turn: answer, synthesize, write, optionally play."""
    response = core.respond_to_text(text)
    print(f"겨울이> {response.text}")
    audio = tts.synthesize(
        SpeechRequest(
            text=response.text,
            emotion=response.prosody.emotion,
            pace=pace,
            energy=response.prosody.energy,
            pitch_offset=response.prosody.pitch_offset,
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"winter-{stamp}.wav"
    path.write_bytes(audio.data)
    print(f"  {path}")
    if should_play:
        play(path)
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
    core = CompanionCore(
        LlamaCppHttpChatModel(base_url=arguments.model_url),
        InMemoryConversationRepository(),
        ConversationContextBuilder(max_messages=12, max_characters=4000),
        identity=identity,
        verbal_style_planner=VerbalStylePlanner(load_verbal_style(arguments.style)),
    )
    flow = arguments.flow_checkpoint or arguments.storage_root / DEFAULT_FLOW
    speaker = arguments.speaker or arguments.storage_root / DEFAULT_SPEAKER
    if not flow.exists() or not speaker.exists():
        # Named rather than left to fail inside a container: without these the
        # voice is not 겨울이's, and that is worth stopping for.
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
    # Bound once rather than splatted from a dict: a dict of mixed value types
    # erases them, and the type checker stops seeing a wrong argument.
    turn = partial(
        speak,
        core,
        tts,
        output_dir=arguments.output_dir,
        pace=arguments.pace,
        should_play=not arguments.no_play,
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
