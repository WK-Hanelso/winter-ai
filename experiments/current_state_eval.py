"""Run review-only current-state held-out scenes against a safe local model."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from time import perf_counter

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.reflection_interpreter import (
    StructuredChatReflectionInterpreter,
)
from companion.contracts import ChatRequest, ChatResult
from companion.current_state_evaluation import (
    EvaluatedStateExtraction,
    ModelStageTrace,
    default_held_out_scenes,
    evaluate,
    locked_held_out_scenes_v1,
    render_markdown,
    summarize,
)
from companion.reflection import ReflectionInterpretationError


class RecordingChatModel:
    def __init__(self, inner: LlamaCppHttpChatModel) -> None:
        self._inner = inner
        self.traces: list[ModelStageTrace] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        result = self._inner.generate(request)
        self.traces.append(ModelStageTrace(_stage_name(request), result.text))
        return result


def run(
    model_url: str,
    *,
    limit: int | None = None,
    scene_labels: frozenset[str] = frozenset(),
    suite: str = "development",
) -> tuple[EvaluatedStateExtraction, ...]:
    chat_model = LlamaCppHttpChatModel(model_url)
    scenes = (
        locked_held_out_scenes_v1()
        if suite == "locked-v1"
        else default_held_out_scenes()
    )
    if scene_labels:
        scenes = tuple(scene for scene in scenes if scene.label in scene_labels)
    selected = scenes if limit is None else scenes[:limit]
    results: list[EvaluatedStateExtraction] = []
    for message_id, scene in enumerate(selected, start=1):
        recording_model = RecordingChatModel(chat_model)
        interpreter = StructuredChatReflectionInterpreter(recording_model)
        started = perf_counter()
        try:
            extraction = interpreter.extract(scene.request(message_id))
        except ReflectionInterpretationError as error:
            results.append(
                EvaluatedStateExtraction(
                    scene,
                    None,
                    str(error),
                    perf_counter() - started,
                    tuple(recording_model.traces),
                )
            )
        else:
            results.append(
                EvaluatedStateExtraction(
                    scene,
                    extraction,
                    None,
                    perf_counter() - started,
                    tuple(recording_model.traces),
                )
            )
    return tuple(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("WINTER_ANALYSIS_LLM_URL"),
        help="safe analysis llama-server URL; defaults to WINTER_ANALYSIS_LLM_URL",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=_positive_int)
    parser.add_argument(
        "--suite",
        choices=("development", "locked-v1"),
        default="development",
    )
    parser.add_argument(
        "--include-raw-trace",
        action="store_true",
        help="include synthetic model JSON for each extraction and verification stage",
    )
    parser.add_argument(
        "--scene",
        action="append",
        choices=tuple(
            scene.label
            for scene in default_held_out_scenes() + locked_held_out_scenes_v1()
        ),
        default=[],
        help="run only this scene; repeat to select more than one",
    )
    arguments = parser.parse_args()
    if not arguments.url:
        parser.error(
            "--url or WINTER_ANALYSIS_LLM_URL is required; "
            "the conversational Orin URL is never selected implicitly"
        )

    results = run(
        arguments.url,
        limit=arguments.limit,
        scene_labels=frozenset(arguments.scene),
        suite=arguments.suite,
    )
    checks = evaluate(results)
    summary = summarize(results, checks)
    report = render_markdown(
        results,
        checks,
        summary,
        include_raw_traces=arguments.include_raw_trace,
    )
    print(report)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report, encoding="utf-8")
    return 0 if summary.candidate_audit_gate_passed else 1


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _stage_name(request: ChatRequest) -> str:
    response_format = request.response_format
    if not isinstance(response_format, dict):
        return "unstructured"
    schema = response_format.get("json_schema")
    if not isinstance(schema, dict):
        return "structured"
    name = schema.get("name")
    return name if isinstance(name, str) else "structured"


if __name__ == "__main__":
    raise SystemExit(main())
