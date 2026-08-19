"""Run locked self-presence scenes against a local Winter Core."""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
from time import perf_counter

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.context import ConversationContextBuilder
from companion.core import CompanionCore
from companion.identity import JsonIdentityRepository
from companion.self_presence_evaluation import (
    LOCKED_V1_SCENES,
    SelfPresenceResult,
    evaluate,
    render_markdown,
)
from companion.verbal_style import VerbalStylePlanner, load_verbal_style


def run(model_url: str, identity_path: Path) -> tuple[SelfPresenceResult, ...]:
    results: list[SelfPresenceResult] = []
    for scene in LOCKED_V1_SCENES:
        with tempfile.TemporaryDirectory(prefix=f"winter-self-{scene.label}-") as directory:
            core = CompanionCore(
                LlamaCppHttpChatModel(model_url),
                SqliteConversationRepository(Path(directory) / "conversations.sqlite"),
                ConversationContextBuilder(max_messages=12, max_characters=4000),
                identity=JsonIdentityRepository(identity_path).load(),
                verbal_style_planner=VerbalStylePlanner(
                    load_verbal_style("reference_conversation")
                ),
            )
            started = perf_counter()
            response = core.respond_to_text(scene.user_text)
            results.append(
                SelfPresenceResult(
                    scene,
                    response.text,
                    response.dialogue_behavior,
                    perf_counter() - started,
                )
            )
    return tuple(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--identity", type=Path, default=Path("data/identity.json"))
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    results = run(arguments.url, arguments.identity)
    checks = tuple(check for result in results for check in evaluate(result))
    report = render_markdown(results, checks)
    print(report)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
