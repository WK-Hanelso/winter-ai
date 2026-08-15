"""Simple persistent CLI for using 겨울이."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import os
from pathlib import Path

from companion.cli.__main__ import run

# The old launcher always ran this module in the dev container, so its data
# directory was hard-coded to /workspace.  Text chat now runs on the Host to
# reach the private Orin SSH tunnel without exposing that tunnel to Docker's
# network.  Keep the container default for direct developer commands while
# allowing the launcher to select the same Host data directory explicitly.
DATA = Path(os.environ.get("WINTER_DATA_DIR", "data"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="겨울이와 대화합니다.")
    parser.add_argument("--backend", choices=("local", "fake"), default="local")
    parser.add_argument(
        "--model-url",
        default=os.environ.get("WINTER_LLM_URL", "http://127.0.0.1:18080"),
    )
    parser.add_argument("--prompt")
    parser.add_argument("--show-history", action="store_true")
    parser.add_argument("--list-memories", action="store_true")
    parser.add_argument("--list-current-state-history", action="store_true")
    parser.add_argument("--current-state-topic")
    parser.add_argument("--list-beliefs", action="store_true")
    parser.add_argument("--list-open-loops", action="store_true")
    parser.add_argument("--list-turn-understanding", action="store_true")
    parser.add_argument("--analyze-pending-turns", action="store_true")
    parser.add_argument("--list-outcomes", action="store_true")
    parser.add_argument("--analyze-pending-outcomes", action="store_true")
    parser.add_argument("--review-outcomes", action="store_true")
    parser.add_argument("--outcome-audit", action="store_true")
    parser.add_argument("--reflect-history", action="store_true")
    parser.add_argument("--review-memory-candidates", action="store_true")
    parser.add_argument("--companion-overview", action="store_true")
    parser.add_argument("--analysis-limit", type=int, default=10)
    parser.add_argument("--review-limit", type=int, default=5)
    parser.add_argument("--reviewer", default="cheonu")
    parser.add_argument("--memory-review-limit", type=int, default=10)
    parser.add_argument("--resolve-open-loop", metavar="ID")
    parser.add_argument("--dismiss-open-loop", metavar="ID")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (DATA / "identity.json").exists():
        print("겨울이 Identity가 없습니다: data/identity.json을 먼저 준비하세요.")
        return 1
    args.identity_path = DATA / "identity.json"
    args.conversation_db = DATA / "conversations.sqlite"
    args.memory_db = DATA / "memories.sqlite"
    args.belief_db = DATA / "beliefs.sqlite"
    args.dialogue_state_db = DATA / "dialogue_state.sqlite"
    args.turn_understanding_db = DATA / "turn_understanding.sqlite"
    args.outcome_db = DATA / "outcomes.sqlite"
    args.reflection_db = DATA / "reflection.sqlite"
    args.memory_kind = "semantic"
    args.context_max_messages = 12
    args.context_max_characters = 4000
    args.verbal_style = "reference_conversation"
    args.show_identity = False
    args.memory_add = args.memory_approve = args.memory_activate = None
    args.memory_deprecate = args.memory_replace = args.memory_delete = None
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
