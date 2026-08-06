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
from companion.identity import IdentityRepositoryError, JsonIdentityRepository
from companion.memory import ActiveMemoryRetriever, MemoryRepositoryError, SqliteMemoryRepository
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
    actions.add_argument("--show-identity", action="store_true")
    actions.add_argument("--list-memories", action="store_true")
    actions.add_argument("--memory-add", help="store explicit user memory as a candidate")
    actions.add_argument("--memory-approve", metavar="ID")
    actions.add_argument("--memory-activate", metavar="ID")
    actions.add_argument("--memory-deprecate", metavar="ID")
    actions.add_argument("--memory-replace", nargs=2, metavar=("ID", "CONTENT"))
    actions.add_argument("--memory-delete", metavar="ID", help="permanently delete an unreferenced memory")
    parser.add_argument("--identity-path", type=Path)
    parser.add_argument("--memory-db", type=Path)
    parser.add_argument("--memory-kind", default="semantic")
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
    try:
        identity_path = getattr(args, "identity_path", None)
        identity = JsonIdentityRepository(identity_path).load() if identity_path else None
    except IdentityRepositoryError as error:
        print(f"Companion identity unavailable: {error}", file=stdout)
        return 1
    try:
        memory_path = getattr(args, "memory_db", None)
        memory_repository = SqliteMemoryRepository(memory_path) if memory_path else None
        memory_retriever = ActiveMemoryRetriever(memory_repository) if memory_repository else None
    except MemoryRepositoryError as error:
        print(f"Memory unavailable: {error}", file=stdout)
        return 1
    if getattr(args, "show_identity", False):
        print(identity.system_message() if identity else "No identity path selected.", file=stdout)
        return 0
    if any(getattr(args, name, None) for name in ("list_memories", "memory_add", "memory_approve", "memory_activate", "memory_deprecate", "memory_replace", "memory_delete")):
        return _handle_memory(args, stdout)
    if getattr(args, "show_history", False):
        return _show_history(repository, stdout)
    core = CompanionCore(
        build_chat_model(args),
        repository,
        ConversationContextBuilder(
            max_messages=getattr(args, "context_max_messages", 12),
            max_characters=getattr(args, "context_max_characters", 4000),
        ),
        identity,
        memory_retriever,
        memory_repository,
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
        if text.startswith("/"):
            _handle_interactive_command(text, args, identity, repository, memory_repository, stdout)
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
    for candidate_id in response.memory_candidate_ids:
        print(f"Memory candidate {candidate_id} is pending review.", file=stdout)
    return 0


def _handle_interactive_command(
    command: str,
    args: argparse.Namespace,
    identity,
    repository: ConversationRepository,
    memory_repository: SqliteMemoryRepository | None,
    stdout: TextIO,
) -> None:
    if command == "/help":
        print("Commands: /status, /history, /memories, /memory {approve|activate|deprecate|delete} <number>, /help, /exit", file=stdout)
    elif command == "/status":
        name = identity.name if identity else "Identity 없음"
        print(f"겨울이: {name} | backend: {args.backend} | conversation: {'persistent' if args.conversation_db else 'temporary'} | memory: {'enabled' if memory_repository else 'disabled'}", file=stdout)
    elif command == "/history":
        _show_history(repository, stdout)
    elif command == "/memories":
        _show_interactive_memories(memory_repository, stdout)
    elif command.startswith("/memory "):
        _handle_interactive_memory_command(command, memory_repository, stdout)
    else:
        print(f"Unknown command: {command}. Use /help.", file=stdout)


def _show_interactive_memories(repository: SqliteMemoryRepository | None, stdout: TextIO) -> None:
    if repository is None:
        print("Memory is disabled.", file=stdout); return
    for index, memory in enumerate(repository.list(), start=1):
        print(f"{index}. [{memory.status}] {memory.kind}: {memory.content}", file=stdout)


def _handle_interactive_memory_command(command: str, repository: SqliteMemoryRepository | None, stdout: TextIO) -> None:
    if repository is None:
        print("Memory is disabled.", file=stdout); return
    parts = command.split()
    if len(parts) != 3 or parts[1] not in {"approve", "activate", "deprecate", "delete"} or not parts[2].isdigit():
        print("Usage: /memory {approve|activate|deprecate|delete} <number>", file=stdout); return
    memories = repository.list(); index = int(parts[2]) - 1
    if index < 0 or index >= len(memories):
        print("Memory number not found.", file=stdout); return
    memory = memories[index]
    try:
        if parts[1] == "delete":
            repository.delete(memory.id); print(f"Memory {parts[2]} was permanently deleted.", file=stdout)
        else:
            updated = repository.transition(memory.id, {"approve": "approved", "activate": "active", "deprecate": "deprecated"}[parts[1]])
            print(f"Memory {parts[2]} is {updated.status}.", file=stdout)
    except MemoryRepositoryError as error:
        print(f"Memory unavailable: {error}", file=stdout)


def _show_history(repository: ConversationRepository, stdout: TextIO) -> int:
    try:
        messages = repository.list_messages()
    except ConversationRepositoryError as error:
        print(f"Conversation storage unavailable: {error}", file=stdout)
        return 1
    for message in messages:
        print(f"{message.role}> {message.content}", file=stdout)
    return 0


def _handle_memory(args: argparse.Namespace, stdout: TextIO) -> int:
    if not args.memory_db:
        print("Memory unavailable: --memory-db is required.", file=stdout); return 1
    try:
        repo = SqliteMemoryRepository(args.memory_db)
        if args.memory_add:
            memory = repo.add_candidate(kind=args.memory_kind, content=args.memory_add)
        elif args.memory_approve:
            memory = repo.transition(args.memory_approve, "approved")
        elif args.memory_activate:
            memory = repo.transition(args.memory_activate, "active")
        elif args.memory_deprecate:
            memory = repo.transition(args.memory_deprecate, "deprecated")
        elif args.memory_replace:
            memory = repo.replace(args.memory_replace[0], args.memory_replace[1])
        elif args.memory_delete:
            deleted = repo.delete(args.memory_delete)
            print(f"Memory {deleted.id} was permanently deleted.", file=stdout)
            return 0
        else:
            for memory in repo.list(): print(f"{memory.id} {memory.status} {memory.kind} supersedes={memory.supersedes}: {memory.content}", file=stdout)
            return 0
    except MemoryRepositoryError as error:
        print(f"Memory unavailable: {error}", file=stdout); return 1
    print(f"Memory {memory.id} is {memory.status}.", file=stdout); return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
