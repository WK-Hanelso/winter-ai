"""Held-out evaluation of what the companion says, not how it says it.

Style has been measured for a while; content never has. This uses the
(prompt, response) pairs built from a conversational source: half are held out,
the companion answers those prompts, and the replies are compared with what the
Reference actually said.

The comparison is deliberately modest. There is one recorded answer per prompt
and a different answer can be just as apt, so the score says how close the reply
came to that particular wording — not whether it was a good reply. A chance
baseline is reported with every score so the number can be read at all.

No pair text reaches stdout.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from companion.adapters.fake import InMemoryConversationRepository
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.content_evaluation import (
    ContentEvaluationError,
    evaluate_content,
    public_content_summary,
)
from companion.core import CompanionCore
from companion.verbal_style import ALLOWED_PROFILES, VerbalStylePlanner, load_verbal_style


def split_pairs(
    pairs: list[dict[str, str]],
    train_ratio: float,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split in recorded order so the held-out half is genuinely later material."""
    if not 0.0 < train_ratio < 1.0:
        raise ContentEvaluationError("train ratio must be between 0 and 1")
    cut = int(len(pairs) * train_ratio)
    if cut == 0 or cut >= len(pairs):
        raise ContentEvaluationError("split produced an empty side")
    return pairs[:cut], pairs[cut:]


def answer(profile_name: str, prompts: list[str], *, model_url: str) -> tuple[str, ...]:
    replies: list[str] = []
    for prompt in prompts:
        core = CompanionCore(
            LlamaCppHttpChatModel(base_url=model_url),
            InMemoryConversationRepository(),
            verbal_style_planner=VerbalStylePlanner(load_verbal_style(profile_name)),
        )
        replies.append(core.respond_to_text(prompt).text)
    return tuple(replies)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-path", type=Path, required=True)
    parser.add_argument("--train-ratio", type=float, default=0.5)
    parser.add_argument("--model-url", default="http://llm:8080")
    parser.add_argument("--report-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        pairs = json.loads(arguments.pairs_path.read_text(encoding="utf-8"))
        if not isinstance(pairs, list):
            raise ContentEvaluationError("pairs file must hold a list")
        _, holdout = split_pairs(pairs, arguments.train_ratio)
        prompts = [str(pair["prompt"]) for pair in holdout]
        recorded = tuple(str(pair["response"]) for pair in holdout)

        evaluations = {}
        for profile_name in ALLOWED_PROFILES:
            replies = answer(profile_name, prompts, model_url=arguments.model_url)
            evaluations[profile_name] = public_content_summary(
                evaluate_content(replies, recorded)
            )
        payload: dict[str, object] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "pair_count": len(pairs),
            "holdout_count": len(holdout),
            "evaluations": evaluations,
        }
    except (ContentEvaluationError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    if arguments.report_path:
        descriptor = os.open(
            arguments.report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")

    payload["status"] = "ok"
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
