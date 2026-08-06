"""Simple persistent CLI for using 겨울이."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from companion.cli.__main__ import run

DATA = Path("/workspace/data")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="겨울이와 대화합니다.")
    parser.add_argument("--backend", choices=("local", "fake"), default="local")
    parser.add_argument("--model-url", default="http://llm:8080")
    parser.add_argument("--prompt")
    parser.add_argument("--show-history", action="store_true")
    parser.add_argument("--list-memories", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (DATA / "identity.json").exists():
        print("겨울이 Identity가 없습니다: data/identity.json을 먼저 준비하세요.")
        return 1
    args.identity_path = DATA / "identity.json"
    args.conversation_db = DATA / "conversations.sqlite"
    args.memory_db = DATA / "memories.sqlite"
    args.memory_kind = "semantic"
    args.context_max_messages = 12
    args.context_max_characters = 4000
    args.show_identity = False
    args.memory_add = args.memory_approve = args.memory_activate = None
    args.memory_deprecate = args.memory_replace = args.memory_delete = None
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
