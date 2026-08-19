"""Evaluate one local Winter Core Model on intent planning and Worker review."""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.work_orchestration import StructuredChatWinterExecutive
from companion.work_orchestration import WorkOrchestrationError
from companion.work_orchestration_evaluation import (
    EvaluatedIntent,
    EvaluatedReview,
    evaluate,
    evaluation_scenes,
    render_markdown,
)


def run(
    model_url: str, *, suite: str = "contract-v2.1"
) -> tuple[tuple[EvaluatedIntent, ...], tuple[EvaluatedReview, ...]]:
    executive = StructuredChatWinterExecutive(LlamaCppHttpChatModel(model_url))
    intent_scenes, review_scenes = evaluation_scenes(suite)
    intents: list[EvaluatedIntent] = []
    reviews: list[EvaluatedReview] = []
    for intent_scene in intent_scenes:
        started = perf_counter()
        try:
            brief = executive.create_brief(intent_scene.request)
        except WorkOrchestrationError as error:
            intents.append(
                EvaluatedIntent(intent_scene, None, str(error), perf_counter() - started)
            )
        else:
            intents.append(
                EvaluatedIntent(intent_scene, brief, None, perf_counter() - started)
            )
    for review_scene in review_scenes:
        started = perf_counter()
        try:
            decision = executive.review(review_scene.brief, review_scene.report)
        except WorkOrchestrationError as error:
            reviews.append(
                EvaluatedReview(review_scene, None, str(error), perf_counter() - started)
            )
        else:
            reviews.append(
                EvaluatedReview(review_scene, decision, None, perf_counter() - started)
            )
    return tuple(intents), tuple(reviews)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model-label")
    parser.add_argument(
        "--suite",
        choices=("contract-v2.1", "semantic-v2"),
        default="contract-v2.1",
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    intents, reviews = run(arguments.url, suite=arguments.suite)
    checks = evaluate(intents, reviews)
    report = render_markdown(
        intents,
        reviews,
        checks,
        model_label=(
            f"{arguments.model_label or 'unknown'}; suite={arguments.suite}"
        ),
    )
    print(report)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report, encoding="utf-8")
    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
