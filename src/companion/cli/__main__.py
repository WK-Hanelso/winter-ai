"""Interactive text CLI for the shared CompanionCore."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence, TextIO

from companion.adapters.fake import AdapterUnavailableError, FakeChatModel, InMemoryConversationRepository
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.sqlite_repository import ConversationRepositoryError, SqliteConversationRepository
from companion.context import ConversationContextBuilder
from companion.core import CompanionCore
from companion.ports import ChatModel, ConversationRepository


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
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prompt", help="run one turn instead of an interactive session")
    actions.add_argument(
        "--show-history",
        action="store_true",
        help="print stored conversation messages and exit",
    )
    parser.add_argument(
        "--conversation-db",
        type=Path,
        help="explicit local SQLite path for persistent conversation history",
    )
    parser.add_argument(
        "--context-max-messages",
        type=_positive_int,
        default=12,
        help="maximum recent messages included in a local chat request (default: 12)",
    )
    parser.add_argument(
        "--context-max-characters",
        type=_positive_int,
        default=4000,
        help="maximum characters included in a local chat request (default: 4000)",
    )
    return parser


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_chat_model(args: argparse.Namespace) -> ChatModel:
    if args.backend == "fake":
        return FakeChatModel()
    return LlamaCppHttpChatModel(base_url=args.model_url)


def build_conversation_repository(args: argparse.Namespace) -> ConversationRepository:
    database_path = getattr(args, "conversation_db", None)
    if database_path is None:
        return InMemoryConversationRepository()
    return SqliteConversationRepository(database_path)


def run(
    args: argparse.Namespace,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
    try:
        repository = build_conversation_repository(args)
    except ConversationRepositoryError as error:
        print(f"Conversation storage unavailable: {error}", file=stdout)
        return 1
    if getattr(args, "show_history", False):
        return _show_history(repository, stdout)
    core = CompanionCore(
        build_chat_model(args),
        repository,
        ConversationContextBuilder(
            max_messages=getattr(args, "context_max_messages", 12),
            max_characters=getattr(args, "context_max_characters", 4000),
        ),
    )
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


def _show_history(repository: ConversationRepository, stdout: TextIO) -> int:
    try:
        messages = repository.list_messages()
    except ConversationRepositoryError as error:
        print(f"Conversation storage unavailable: {error}", file=stdout)
        return 1
    for message in messages:
        print(f"{message.role}> {message.content}", file=stdout)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
