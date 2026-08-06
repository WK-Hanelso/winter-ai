"""Interactive text CLI for the shared CompanionCore."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence, TextIO

from companion.adapters.fake import AdapterUnavailableError, FakeChatModel, InMemoryConversationRepository
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.core import CompanionCore
from companion.ports import ChatModel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("fake", "local"),
        default="fake",
        help="selected ChatModel backend; fake is explicit default for offline development",
    )
    parser.add_argument(
        "--model-url",
        default="http://llm:8080",
        help="local llama.cpp server URL when --backend local is selected",
    )
    parser.add_argument("--prompt", help="run one turn instead of an interactive session")
    return parser


def build_chat_model(args: argparse.Namespace) -> ChatModel:
    if args.backend == "fake":
        return FakeChatModel()
    return LlamaCppHttpChatModel(base_url=args.model_url)


def run(
    args: argparse.Namespace,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
    core = CompanionCore(build_chat_model(args), InMemoryConversationRepository())
    if args.prompt is not None:
        return _run_turn(core, args.prompt, stdout)

    print("winter-ai CLI. 종료하려면 /exit 를 입력하세요.", file=stdout)
    while True:
        print("You> ", end="", file=stdout, flush=True)
        text = stdin.readline()
        if not text:
            return 0
        text = text.strip()
        if text == "/exit":
            return 0
        if not text:
            continue
        if _run_turn(core, text, stdout):
            return 1


def _run_turn(core: CompanionCore, text: str, stdout: TextIO) -> int:
    try:
        response = core.respond_to_text(text)
    except AdapterUnavailableError as error:
        print(f"Companion unavailable: {error}", file=stdout)
        return 1
    print(f"Companion> {response.text}", file=stdout)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
