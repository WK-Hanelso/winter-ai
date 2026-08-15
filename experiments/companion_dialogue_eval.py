"""Run a local multi-session conversation and write a human-readable report."""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
from time import perf_counter

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.beliefs import ActiveBeliefRetriever, SqliteBeliefRepository
from companion.context import ConversationContextBuilder
from companion.core import CompanionCore
from companion.dialogue_evaluation import EvaluatedTurn, evaluate, render_markdown
from companion.identity import JsonIdentityRepository
from companion.memory import ActiveMemoryRetriever, SqliteMemoryRepository
from companion.open_loops import ActiveOpenLoopRetriever, SqliteOpenLoopRepository
from companion.verbal_style import VerbalStylePlanner, load_verbal_style


def build_core(root: Path, model_url: str, identity_path: Path) -> CompanionCore:
    memory = SqliteMemoryRepository(root / "memories.sqlite")
    belief = SqliteBeliefRepository(root / "beliefs.sqlite")
    open_loops = SqliteOpenLoopRepository(root / "dialogue_state.sqlite")
    return CompanionCore(
        LlamaCppHttpChatModel(model_url),
        SqliteConversationRepository(root / "conversations.sqlite"),
        ConversationContextBuilder(max_messages=12, max_characters=4000),
        identity=JsonIdentityRepository(identity_path).load(),
        memory_retriever=ActiveMemoryRetriever(memory),
        memory_repository=memory,
        belief_retriever=ActiveBeliefRetriever(belief),
        open_loop_repository=open_loops,
        open_loop_retriever=ActiveOpenLoopRetriever(open_loops),
        verbal_style_planner=VerbalStylePlanner(
            load_verbal_style("reference_conversation")
        ),
    )


def ask(core: CompanionCore, label: str, text: str) -> EvaluatedTurn:
    started = perf_counter()
    response = core.respond_to_text(text)
    return EvaluatedTurn(
        label=label,
        user=text,
        winter=response.text,
        behavior=response.dialogue_behavior,
        seconds=perf_counter() - started,
        automatic_memory_ids=response.automatic_memory_ids,
    )


def run(model_url: str, identity_path: Path) -> tuple[EvaluatedTurn, ...]:
    with tempfile.TemporaryDirectory(prefix="winter-dialogue-eval-") as directory:
        root = Path(directory)
        first_session = build_core(root, model_url, identity_path)
        turns = [
            ask(
                first_session,
                "distress",
                "같은 실패를 계속 반복하는 것 같아서 너무 답답해.",
            ),
            ask(
                first_session,
                "stance",
                "목소리와 대화 기억 중 지금 뭐가 더 중요하다고 생각해?",
            ),
            ask(
                first_session,
                "defer",
                "대화 기억은 내일 다시 이어서 보자.",
            ),
        ]

        # New Core instances are deliberate: continuity must come from storage,
        # not objects left alive in one Python process.
        second_session = build_core(root, model_url, identity_path)
        turns.append(ask(second_session, "continuation", "아까 얘기 이어가자."))
        turns.append(
            ask(
                second_session,
                "memory_save",
                "나는 해결책보다 상황을 먼저 이해해주는 대화를 좋아해.",
            )
        )

        third_session = build_core(root, model_url, identity_path)
        turns.append(
            ask(
                third_session,
                "memory_recall",
                "내가 어떤 방식의 대화를 좋아한다고 했지?",
            )
        )
        return tuple(turns)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--identity", type=Path, default=Path("data/identity.json"))
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    turns = run(arguments.url, arguments.identity)
    report = render_markdown(turns, evaluate(turns))
    print(report)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
