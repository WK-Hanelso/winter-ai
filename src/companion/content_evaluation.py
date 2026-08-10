"""Compare a generated reply with what the Reference actually said.

Style has been measured for a while; content never has. This module answers a
narrower question than it might appear to: **how close is the reply to the one
the Reference gave**, not whether the reply is good.

That distinction is the whole design. There is exactly one recorded answer per
question, and a different answer can be just as apt, so a low score is not
evidence of a bad reply. Two baselines are reported alongside every score so the
number can be read at all:

* a **chance floor** measured from the Reference's own answers compared with
  each other. Two unrelated Korean sentences already share particles and
  endings, so agreement does not start at zero;
* a **ceiling of 1.0**, exact reproduction.

The reported position says how far from chance the reply moved toward the
recorded wording. It is not a quality score.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any

CONTENT_EVALUATION_SCHEMA_VERSION = 1

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


class ContentEvaluationError(RuntimeError):
    """Raised when a content comparison cannot be reported honestly."""


@dataclass(frozen=True)
class ContentScore:
    index: int
    similarity: float
    token_overlap: float
    generated_words: int
    reference_words: int


@dataclass(frozen=True)
class ContentEvaluation:
    schema_version: int
    scores: tuple[ContentScore, ...]
    mean_similarity: float
    mean_token_overlap: float
    chance_similarity: float

    @property
    def position(self) -> float | None:
        """How far the reply moved from chance toward exact reproduction, 0..1.

        None when chance agreement is already near 1.0, which happens if every
        recorded answer is nearly the same sentence. The metric separates
        nothing there and no score from it should be quoted.
        """
        span = 1.0 - self.chance_similarity
        if span <= 1e-9:
            return None
        return (self.mean_similarity - self.chance_similarity) / span


def normalise(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalise(left), normalise(right)).ratio()


def token_overlap(left: str, right: str) -> float:
    """Share of the reference's words that also appear in the reply.

    Sequence similarity rewards matching order; this does not. Reporting both
    keeps a reply that says the same things in another order from looking as
    distant as one that says nothing similar.
    """
    reference_tokens = set(_WORD.findall(right.lower()))
    if not reference_tokens:
        return 0.0
    generated_tokens = set(_WORD.findall(left.lower()))
    return len(reference_tokens & generated_tokens) / len(reference_tokens)


def evaluate_content(
    generated: tuple[str, ...],
    reference: tuple[str, ...],
) -> ContentEvaluation:
    """Score replies against recorded answers, with both baselines."""
    if len(generated) != len(reference):
        raise ContentEvaluationError("generated and reference counts must match")
    if len(generated) < 2:
        raise ContentEvaluationError("need at least two pairs to build baselines")

    scores = tuple(
        ContentScore(
            index=index,
            similarity=similarity(reply, answer),
            token_overlap=token_overlap(reply, answer),
            generated_words=len(_WORD.findall(reply)),
            reference_words=len(_WORD.findall(answer)),
        )
        for index, (reply, answer) in enumerate(zip(generated, reference, strict=True))
    )
    # Chance agreement: what unrelated answers already score against each other.
    # Measured on the recorded answers alone so the reply cannot influence it.
    between_references = tuple(
        similarity(reference[index], reference[(index + 1) % len(reference)])
        for index in range(len(reference))
    )
    return ContentEvaluation(
        schema_version=CONTENT_EVALUATION_SCHEMA_VERSION,
        scores=scores,
        mean_similarity=sum(score.similarity for score in scores) / len(scores),
        mean_token_overlap=sum(score.token_overlap for score in scores) / len(scores),
        chance_similarity=sum(between_references) / len(between_references),
    )


def public_content_summary(evaluation: ContentEvaluation) -> dict[str, Any]:
    """Counts and scores only. No reply or recorded answer appears here."""
    position = evaluation.position
    return {
        "schema_version": evaluation.schema_version,
        "pair_count": len(evaluation.scores),
        "mean_similarity": round(evaluation.mean_similarity, 4),
        "mean_token_overlap": round(evaluation.mean_token_overlap, 4),
        "chance_similarity": round(evaluation.chance_similarity, 4),
        "position_above_chance": None if position is None else round(position, 4),
        # When chance agreement is already near 1.0 the metric separates nothing
        # and quoting a score from it would be misleading.
        "chance_leaves_room": position is not None,
        "mean_generated_words": round(
            sum(score.generated_words for score in evaluation.scores)
            / len(evaluation.scores),
            2,
        ),
        "mean_reference_words": round(
            sum(score.reference_words for score in evaluation.scores)
            / len(evaluation.scores),
            2,
        ),
    }
