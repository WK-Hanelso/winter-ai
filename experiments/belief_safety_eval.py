"""Run locked Winter Belief semantic-safety scenes against a local model."""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

from companion.adapters.belief_reflection_interpreter import (
    StructuredChatBeliefSafetyReviewer,
)
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.belief_reflection import BeliefReflectionError
from companion.belief_reflection_evaluation import (
    LOCKED_V1_SCENES,
    EvaluatedBeliefSafety,
    activation_gates,
    render_markdown,
)


def run(model_url: str) -> tuple[EvaluatedBeliefSafety, ...]:
    reviewer = StructuredChatBeliefSafetyReviewer(LlamaCppHttpChatModel(model_url))
    results = []
    for scene in LOCKED_V1_SCENES:
        started = perf_counter()
        try:
            review = reviewer.review(scene.request, scene.proposal)
        except BeliefReflectionError as error:
            results.append(
                EvaluatedBeliefSafety(
                    scene, None, str(error), perf_counter() - started
                )
            )
        else:
            results.append(
                EvaluatedBeliefSafety(
                    scene, review, None, perf_counter() - started
                )
            )
    return tuple(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    results = run(arguments.url)
    gates = activation_gates(results)
    report = render_markdown(results, gates)
    print(report)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report, encoding="utf-8")
    return 0 if all(gate.passed for gate in gates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
