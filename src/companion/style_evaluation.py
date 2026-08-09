"""Measure how far a generated speech style is from the Reference.

Everything checked so far has been anecdotal: one question, one answer, a
judgement by eye. This module turns the question into a number, because the
decision it feeds — whether to train a model at all (roadmap Order 10) — cannot
be made on impressions.

What this measures: the *distribution* of style traits. Register, utterance
length, hesitation rate, ending mix.

What it cannot measure: whether the content is right. A monologue source has no
(prompt, response) pairs, so there is nothing to compare a reply against. That
gap closes only after speaker separation, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from companion.reference_speech_style import EndingProfile, SpeechStyleProfile

STYLE_EVALUATION_SCHEMA_VERSION = 1

# Weights say what "sounding like someone" means here. Register dominates:
# plain and polite speech are not interchangeable, and getting it wrong is the
# most audible way to sound like a different person. Length is next because it
# is what a reader notices before word choice.
TRAIT_WEIGHTS = {
    "register": 0.4,
    "utterance_length": 0.3,
    "hesitation": 0.2,
    "ending_mix": 0.1,
}


@dataclass(frozen=True)
class TraitDistance:
    trait: str
    candidate: float | None
    reference: float | None
    distance: float | None
    weight: float


@dataclass(frozen=True)
class StyleDistance:
    schema_version: int
    candidate_label: str
    reference_label: str
    traits: tuple[TraitDistance, ...]
    total_distance: float
    comparable_weight: float

    @property
    def is_complete(self) -> bool:
        """False when a trait had to be skipped, so totals are not comparable."""
        return abs(self.comparable_weight - 1.0) < 1e-9


def style_distance(
    candidate: SpeechStyleProfile,
    reference: SpeechStyleProfile,
) -> StyleDistance:
    """Return a 0..1 distance where 0 means the traits match.

    Traits that cannot be computed on either side are skipped rather than
    scored as zero: a missing measurement is not agreement. ``is_complete``
    reports whether anything had to be skipped.
    """
    traits = (
        _register_distance(candidate, reference),
        _length_distance(candidate, reference),
        _hesitation_distance(candidate, reference),
        _ending_mix_distance(candidate, reference),
    )
    scored = [trait for trait in traits if trait.distance is not None]
    # Length and hesitation always yield a number, so at least those weights are
    # present and the division below is safe. Register and ending mix can be
    # absent when a transcript classifies no sentence endings at all.
    comparable = sum(trait.weight for trait in scored)
    total = sum(trait.distance * trait.weight for trait in scored if trait.distance is not None)
    return StyleDistance(
        schema_version=STYLE_EVALUATION_SCHEMA_VERSION,
        candidate_label=candidate.label,
        reference_label=reference.label,
        traits=traits,
        total_distance=total / comparable,
        comparable_weight=comparable,
    )


def public_distance_summary(distance: StyleDistance) -> dict[str, Any]:
    return {
        "schema_version": distance.schema_version,
        "candidate": distance.candidate_label,
        "reference": distance.reference_label,
        "total_distance": round(distance.total_distance, 4),
        "comparable_weight": round(distance.comparable_weight, 3),
        "is_complete": distance.is_complete,
        "traits": [
            {
                "trait": trait.trait,
                "candidate": None if trait.candidate is None else round(trait.candidate, 4),
                "reference": None if trait.reference is None else round(trait.reference, 4),
                "distance": None if trait.distance is None else round(trait.distance, 4),
                "weight": trait.weight,
            }
            for trait in distance.traits
        ],
    }


def _register_distance(
    candidate: SpeechStyleProfile,
    reference: SpeechStyleProfile,
) -> TraitDistance:
    left = candidate.endings.polite_ratio
    right = reference.endings.polite_ratio
    return TraitDistance(
        trait="register",
        candidate=left,
        reference=right,
        distance=None if left is None or right is None else abs(left - right),
        weight=TRAIT_WEIGHTS["register"],
    )


def _length_distance(
    candidate: SpeechStyleProfile,
    reference: SpeechStyleProfile,
) -> TraitDistance:
    left = candidate.mean_words_per_utterance
    right = reference.mean_words_per_utterance
    return TraitDistance(
        trait="utterance_length",
        candidate=left,
        reference=right,
        distance=_relative_gap(left, right),
        weight=TRAIT_WEIGHTS["utterance_length"],
    )


def _hesitation_distance(
    candidate: SpeechStyleProfile,
    reference: SpeechStyleProfile,
) -> TraitDistance:
    left = candidate.filler_per_100_words
    right = reference.filler_per_100_words
    return TraitDistance(
        trait="hesitation",
        candidate=left,
        reference=right,
        distance=_relative_gap(left, right),
        weight=TRAIT_WEIGHTS["hesitation"],
    )


def _ending_mix_distance(
    candidate: SpeechStyleProfile,
    reference: SpeechStyleProfile,
) -> TraitDistance:
    left = _ending_shares(candidate.endings)
    right = _ending_shares(reference.endings)
    if left is None or right is None:
        distance = None
    else:
        # Total variation distance: half the sum of absolute share differences,
        # which keeps the result in 0..1 like every other trait here.
        distance = sum(abs(a - b) for a, b in zip(left, right, strict=True)) / 2
    return TraitDistance(
        trait="ending_mix",
        candidate=None,
        reference=None,
        distance=distance,
        weight=TRAIT_WEIGHTS["ending_mix"],
    )


def _ending_shares(endings: EndingProfile) -> tuple[float, float, float] | None:
    total = endings.classified
    if not total:
        return None
    return (
        endings.polite_formal / total,
        endings.polite_casual / total,
        endings.plain / total,
    )


def _relative_gap(candidate: float, reference: float) -> float:
    """Scale the gap by the reference so traits with different units compare.

    Two zeros are agreement, not a missing measurement: a speaker who never
    hesitates matched by a companion that never hesitates is a perfect score.
    Capped at 1.0 so one wildly off trait cannot dominate the weighted total.
    """
    if reference <= 0:
        return 0.0 if candidate <= 0 else 1.0
    return min(abs(candidate - reference) / reference, 1.0)
