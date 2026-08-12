"""Which part makes 겨울이's answers bad: the model, the identity, or the style.

Her voice is settled and her answers are not, and there were three suspects —
the model's Korean, the identity and style instructions competing for attention,
and the length cap breaking sentences. Guessing between them is how the voice
path lost a day, so this asks the same ten questions under four conditions and
prints the answers side by side.

The first run (2026-08-12) cleared two of the three suspects. The instructions
are not fighting: the full combination gave the best answers of the four.
Removing the style profile put her straight back to being an assistant —
paragraphs, 존댓말, emoji, "당신" — and removing the identity had her answer
"겨울이 뭐해?" with "겨울이야. 추워.", having read her name as the season.

What it left is two real problems. The identity tells her she has no life
("오늘 하루 어땠어?" → "지금은 하루를 보내지 않아"), which is the opposite of
what an OC needs. And under the length cap the model produces broken Korean
("이거 어떻게 생각해요 맞아요?"), which no instruction will fix.

Run it again after changing either, and compare against those answers.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import time

from companion.adapters.fake import InMemoryConversationRepository
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.context import ConversationContextBuilder
from companion.core import CompanionCore
from companion.identity import JsonIdentityRepository
from companion.verbal_style import VerbalStylePlanner, load_verbal_style

# Ordinary things a person says to someone they live with. Deliberately not
# tasks: what is being judged is whether she talks like a person, and a question
# with a correct answer hides that.
QUESTIONS = (
    "오늘 하루 어땠어?",
    "발표 망쳤어",
    "겨울이 뭐해?",
    "나 좀 피곤해",
    "밥 먹었어?",
    "너는 뭐가 제일 좋아?",
    "내일 비 온대",
    "요즘 잠을 잘 못 자",
    "이거 어떻게 생각해?",
    "고마워",
)

CONDITIONS = (
    ("1-지금 그대로", "reference_conversation", True),
    ("2-스타일 없이", "base", True),
    ("3-정체성 없이", "reference_conversation", False),
    ("4-둘 다 없이", "base", False),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-url", default="http://127.0.0.1:8080")
    parser.add_argument("--identity-path", type=Path, default=Path("data/identity.json"))
    parser.add_argument("--width", type=int, default=70, help="답변을 자를 길이")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    model = LlamaCppHttpChatModel(base_url=arguments.model_url)
    identity = JsonIdentityRepository(arguments.identity_path).load()

    for name, profile, with_identity in CONDITIONS:
        print(f"\n===== {name} =====")
        for question in QUESTIONS:
            # A fresh conversation each time: an answer that only makes sense
            # after the previous one would make the conditions incomparable.
            core = CompanionCore(
                model,
                InMemoryConversationRepository(),
                ConversationContextBuilder(max_messages=12, max_characters=4000),
                identity=identity if with_identity else None,
                verbal_style_planner=VerbalStylePlanner(load_verbal_style(profile)),
            )
            started = time.perf_counter()
            answer = core.respond_to_text(question).text.replace("\n", " ")
            print(
                f"  {question:14s} ({time.perf_counter() - started:4.1f}초) "
                f"→ {answer[: arguments.width]}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
