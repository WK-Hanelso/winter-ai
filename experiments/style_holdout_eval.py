"""Held-out evaluation of a verbal style profile against the Reference.

Two things happen here, in order:

1. **Contamination check.** The shipped profile was derived from the whole
   transcript, so evaluating on any part of it is marking your own homework.
   The transcript is split, traits are re-derived from the train half only, and
   the two are compared. A small gap means the shipped profile can stand.
2. **Style distance.** Fixed probe questions are answered under each profile and
   the generated speech is compared with the held-out half of the Reference.

What this cannot measure: whether the content is right. A monologue has no
(prompt, response) pairs. That gap closes after speaker separation, not here.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from companion.adapters.fake import InMemoryConversationRepository
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.core import CompanionCore
from companion.reference_speech_style import (
    SpeechStyleProfile,
    profile_transcript,
    public_style_summary,
)
from companion.reference_subtitle_probe import SubtitleCue, parse_webvtt
from companion.style_evaluation import (
    distance_interval,
    public_distance_summary,
    public_interval_summary,
    style_distance,
)
from companion.verbal_style import ALLOWED_PROFILES, VerbalStylePlanner, load_verbal_style

# Everyday prompts a companion would actually get. Deliberately mundane: the
# question is how it speaks, not what it knows.
#
# Thirty rather than ten. Once the register is drawn per turn, ten answers leave
# the rate traits — utterance length, hesitation — dominated by which registers
# happened to come up. Widening the probe reduces that noise; it does not tune
# anything toward the metric.
PROBE_QUESTIONS = (
    "오늘 어땠어?",
    "요즘 뭐가 제일 재밌어?",
    "나 좀 피곤한데 어떡하지?",
    "주말에 뭐 할까?",
    "그 얘기 어떻게 생각해?",
    "밥 뭐 먹을까?",
    "요즘 잠을 잘 못 자",
    "기분이 좀 그래",
    "내일 발표가 있어",
    "고마워",
    "지금 뭐 해?",
    "커피 마실까 말까",
    "그거 재밌었어?",
    "오늘 좀 추운 것 같아",
    "나 방금 실수했어",
    "이번 주 어떻게 보냈어?",
    "심심하다",
    "그 사람 어떤 것 같아?",
    "운동 좀 해야 할까?",
    "요즘 뭐 듣고 있어?",
    "일찍 잘까?",
    "그냥 좀 답답해",
    "이거 어떻게 생각해?",
    "다음 주에 시간 돼?",
    "배고파",
    "오랜만이야",
    "그때 기억나?",
    "좀 도와줄래?",
    "나 잘하고 있는 걸까",
    "잘 자",
)


def split_cues(
    cues: tuple[SubtitleCue, ...],
    train_ratio: float,
) -> tuple[tuple[SubtitleCue, ...], tuple[SubtitleCue, ...]]:
    """Split on time order so the held-out half is genuinely unseen material."""
    if not 0.0 < train_ratio < 1.0:
        raise ValueError("train ratio must be between 0 and 1")
    ordered = tuple(sorted(cues, key=lambda cue: cue.start_seconds))
    cut = int(len(ordered) * train_ratio)
    if cut == 0 or cut == len(ordered):
        raise ValueError("split produced an empty side")
    return ordered[:cut], ordered[cut:]


def generate_responses(
    profile_name: str,
    questions: Sequence[str],
    *,
    model_url: str,
) -> tuple[str, ...]:
    """Answer every probe question under one style profile."""
    responses: list[str] = []
    for question in questions:
        core = CompanionCore(
            LlamaCppHttpChatModel(base_url=model_url),
            InMemoryConversationRepository(),
            verbal_style_planner=VerbalStylePlanner(load_verbal_style(profile_name)),
        )
        responses.append(core.respond_to_text(question).text)
    return tuple(responses)


def profile_responses(label: str, responses: Sequence[str]) -> SpeechStyleProfile:
    cues = tuple(
        SubtitleCue(float(index), float(index + 1), response)
        for index, response in enumerate(responses)
        if response.strip()
    )
    return profile_transcript(label, cues)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--model-url", default="http://llm:8080")
    parser.add_argument("--report-path", type=Path)
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="generation rounds; one round cannot be compared with the floor",
    )
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="run only the contamination check, without contacting a model",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    cues = parse_webvtt(arguments.transcript.read_text(encoding="utf-8"))
    train_cues, holdout_cues = split_cues(cues, arguments.train_ratio)

    whole = profile_transcript("reference-whole", cues)
    train = profile_transcript("reference-train", train_cues)
    holdout = profile_transcript("reference-holdout", holdout_cues)

    payload: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "train_ratio": arguments.train_ratio,
        "cue_counts": {
            "whole": len(cues),
            "train": len(train_cues),
            "holdout": len(holdout_cues),
        },
        # If the traits the profile encodes survive the split, deriving them
        # from the whole transcript did not meaningfully leak the answer.
        "contamination_check": public_distance_summary(style_distance(train, whole)),
        # The speaker is not identical to themselves across the broadcast. This
        # is the floor: a generated style cannot sensibly score closer to the
        # held-out half than the speaker's own earlier half does.
        "reference_self_distance": public_distance_summary(
            style_distance(train, holdout)
        ),
        "reference": {
            "whole": public_style_summary(whole),
            "train": public_style_summary(train),
            "holdout": public_style_summary(holdout),
        },
    }

    if not arguments.skip_generation:
        if arguments.repeats < 1:
            raise ValueError("repeats must be at least 1")
        floor = style_distance(train, holdout).total_distance
        evaluations = {}
        for profile_name in ALLOWED_PROFILES:
            per_run: list[float] = []
            pooled: list[str] = []
            for _ in range(arguments.repeats):
                responses = generate_responses(
                    profile_name, PROBE_QUESTIONS, model_url=arguments.model_url
                )
                pooled.extend(responses)
                per_run.append(
                    style_distance(
                        profile_responses(f"generated-{profile_name}", responses),
                        holdout,
                    ).total_distance
                )
            # Rate traits are counts over a small sample, so one round's filler
            # rate swings wildly. Pooling every round's answers gives the rate a
            # sample large enough to mean something; the per-run spread is kept
            # separately to say how uncertain that is.
            pooled_profile = profile_responses(f"pooled-{profile_name}", pooled)
            evaluations[profile_name] = {
                "pooled_style": public_style_summary(pooled_profile),
                "pooled_distance": public_distance_summary(
                    style_distance(pooled_profile, holdout)
                ),
                "per_run_distances": [round(value, 4) for value in per_run],
                "interval": public_interval_summary(
                    distance_interval(tuple(per_run)), floor=floor
                ),
            }
        payload["evaluations"] = evaluations

    if arguments.report_path:
        descriptor = os.open(
            arguments.report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
