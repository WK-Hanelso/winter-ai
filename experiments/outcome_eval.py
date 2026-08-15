"""Run synthetic held-out OutcomeEvaluator scenes against a local model."""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.outcome_interpreter import StructuredChatOutcomeInterpreter
from companion.outcome import OutcomeInterpretationError
from companion.outcome_evaluation import (
    EvaluatedOutcome,
    activation_gates,
    default_calibration_scenes,
    default_held_out_scenes,
    evaluate,
    locked_v2_scenes,
    render_markdown,
)


def run(
    model_url: str,
    *,
    repeats: int = 1,
    limit: int | None = None,
    scene_labels: frozenset[str] = frozenset(),
    suite: str = "heldout",
) -> tuple[EvaluatedOutcome, ...]:
    interpreter = StructuredChatOutcomeInterpreter(LlamaCppHttpChatModel(model_url))
    scenes = (
        default_calibration_scenes()
        if suite == "calibration"
        else locked_v2_scenes()
        if suite == "locked-v2"
        else default_held_out_scenes()
    )
    if scene_labels:
        scenes = tuple(scene for scene in scenes if scene.label in scene_labels)
    if limit is not None:
        scenes = scenes[:limit]
    results: list[EvaluatedOutcome] = []
    for repeat in range(1, repeats + 1):
        for scene in scenes:
            started = perf_counter()
            try:
                assessment = interpreter.evaluate(scene.request)
            except OutcomeInterpretationError as error:
                results.append(
                    EvaluatedOutcome(
                        scene, None, str(error), perf_counter() - started, repeat
                    )
                )
            else:
                results.append(
                    EvaluatedOutcome(
                        scene,
                        assessment,
                        None,
                        perf_counter() - started,
                        repeat,
                    )
                )
    return tuple(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=_positive_int)
    parser.add_argument("--repeats", type=_positive_int, default=1)
    parser.add_argument(
        "--suite",
        choices=("heldout", "locked-v2", "calibration"),
        default="heldout",
    )
    parser.add_argument(
        "--scene",
        action="append",
        choices=tuple(
            scene.label
            for scene in (
                *default_held_out_scenes(),
                *locked_v2_scenes(),
                *default_calibration_scenes(),
            )
        ),
        default=[],
        help="run only this scene; repeat to select more than one",
    )
    arguments = parser.parse_args()

    results = run(
        arguments.url,
        repeats=arguments.repeats,
        limit=arguments.limit,
        scene_labels=frozenset(arguments.scene),
        suite=arguments.suite,
    )
    checks = evaluate(results)
    gates = activation_gates(results)
    report = render_markdown(results, checks, gates)
    print(report)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report, encoding="utf-8")
    return 0 if all(gate.passed for gate in gates) else 1


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
