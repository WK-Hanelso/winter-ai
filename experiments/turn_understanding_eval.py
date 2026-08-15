"""Run synthetic held-out TurnUnderstanding scenes against a local model."""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.turn_interpreter import StructuredChatTurnInterpreter
from companion.turn_understanding import TurnInterpretationError
from companion.turn_understanding_evaluation import (
    EvaluatedUnderstanding,
    default_held_out_scenes,
    evaluate,
    render_markdown,
)


def run(
    model_url: str,
    *,
    limit: int | None = None,
    scene_labels: frozenset[str] = frozenset(),
) -> tuple[EvaluatedUnderstanding, ...]:
    interpreter = StructuredChatTurnInterpreter(LlamaCppHttpChatModel(model_url))
    results: list[EvaluatedUnderstanding] = []
    scenes = default_held_out_scenes()
    if scene_labels:
        scenes = tuple(scene for scene in scenes if scene.label in scene_labels)
    for scene in scenes if limit is None else scenes[:limit]:
        started = perf_counter()
        try:
            understanding = interpreter.interpret(scene.request)
        except TurnInterpretationError as error:
            results.append(
                EvaluatedUnderstanding(
                    scene, None, str(error), perf_counter() - started
                )
            )
        else:
            results.append(
                EvaluatedUnderstanding(
                    scene, understanding, None, perf_counter() - started
                )
            )
    return tuple(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=_positive_int)
    parser.add_argument(
        "--scene",
        action="append",
        choices=tuple(scene.label for scene in default_held_out_scenes()),
        default=[],
        help="run only this scene; repeat to select more than one",
    )
    arguments = parser.parse_args()

    results = run(
        arguments.url,
        limit=arguments.limit,
        scene_labels=frozenset(arguments.scene),
    )
    checks = evaluate(results)
    report = render_markdown(results, checks)
    print(report)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report, encoding="utf-8")
    return 0 if all(check.passed for check in checks) else 1


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
