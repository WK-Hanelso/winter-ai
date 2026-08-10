"""Decide which transcript segments belong to the Reference speaker.

This is speaker *verification*, not diarization. The question is not "how many
people are here and when did each speak" but "is this segment the Reference".
A clean single-speaker recording already exists, so it serves as the enrolment
sample and the harder problem never has to be solved.

Framing it this way also supplies its own controls. The single-speaker source
must come back almost entirely as the Reference; a conversational source must
split into two groups. If the similarity scores form one hump instead of two,
the method failed and no threshold will rescue it — that outcome is reported
rather than papered over.

Nothing here loads audio or a model. Embeddings arrive from an adapter so the
decision rules stay testable without torch.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

SPEAKER_VERIFICATION_SCHEMA_VERSION = 1

# Below this, an embedding is dominated by whatever noise shares the window.
MINIMUM_RELIABLE_SECONDS = 1.5


class SpeakerVerificationError(RuntimeError):
    """Raised when a verification result cannot be reported honestly."""


@dataclass(frozen=True)
class SegmentScore:
    index: int
    start_seconds: float
    end_seconds: float
    similarity: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def is_reliable(self) -> bool:
        return self.duration_seconds >= MINIMUM_RELIABLE_SECONDS


@dataclass(frozen=True)
class ScoreDistribution:
    count: int
    mean: float
    median: float
    stdev: float
    minimum: float
    maximum: float


@dataclass(frozen=True)
class SeparationCheck:
    """Whether the scores actually split into two groups."""

    threshold: float | None
    low_count: int
    high_count: int
    low_mean: float | None
    high_mean: float | None
    separation: float | None
    is_bimodal: bool
    reason: str


@dataclass(frozen=True)
class VerificationReport:
    schema_version: int
    source_id: str
    distribution: ScoreDistribution
    separation: SeparationCheck
    reliable_count: int
    short_count: int


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise SpeakerVerificationError("embeddings must have the same dimension")
    if not left:
        raise SpeakerVerificationError("embeddings must not be empty")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise SpeakerVerificationError("embeddings must not be all zero")
    return dot / (left_norm * right_norm)


def describe(scores: tuple[SegmentScore, ...]) -> ScoreDistribution:
    if not scores:
        raise SpeakerVerificationError("no segment scores to describe")
    values = sorted(score.similarity for score in scores)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    middle = len(values) // 2
    median = (
        values[middle]
        if len(values) % 2
        else (values[middle - 1] + values[middle]) / 2
    )
    return ScoreDistribution(
        count=len(values),
        mean=mean,
        median=median,
        stdev=math.sqrt(variance),
        minimum=values[0],
        maximum=values[-1],
    )


def find_threshold(
    scores: tuple[SegmentScore, ...],
    *,
    minimum_separation: float = 0.15,
    minimum_group_share: float = 0.1,
    minimum_group_count: int = 3,
) -> SeparationCheck:
    """Split the scores in two by maximising between-group separation.

    Otsu's criterion on a one-dimensional score set: try every midpoint, keep
    the split whose group means are furthest apart. The result is only accepted
    when both groups are large enough and far enough apart to be two speakers
    rather than one speaker's natural spread.

    Size is checked as a share *and* an absolute count. A share alone lets a
    single outlier pass as a speaker whenever the run is short: one segment out
    of seven is 14%.
    """
    if not scores:
        raise SpeakerVerificationError("no segment scores to threshold")
    values = sorted(score.similarity for score in scores)
    if len(values) < 4:
        return SeparationCheck(
            threshold=None,
            low_count=0,
            high_count=len(values),
            low_mean=None,
            high_mean=None,
            separation=None,
            is_bimodal=False,
            reason="too few segments to judge separation",
        )

    best: tuple[float, float, int] | None = None
    for cut in range(1, len(values)):
        low = values[:cut]
        high = values[cut:]
        separation = sum(high) / len(high) - sum(low) / len(low)
        if best is None or separation > best[0]:
            best = (separation, (values[cut - 1] + values[cut]) / 2, cut)
    assert best is not None
    separation, threshold, cut = best

    low = values[:cut]
    high = values[cut:]
    smaller = min(len(low), len(high))
    share = smaller / len(values)
    if separation < minimum_separation:
        reason = "score spread is one group, not two speakers"
    elif share < minimum_group_share or smaller < minimum_group_count:
        reason = "one side holds too few segments to be a speaker"
    else:
        reason = "two groups separated"
    return SeparationCheck(
        threshold=threshold,
        low_count=len(low),
        high_count=len(high),
        low_mean=sum(low) / len(low),
        high_mean=sum(high) / len(high),
        separation=separation,
        is_bimodal=reason == "two groups separated",
        reason=reason,
    )


def verify_segments(
    source_id: str,
    scores: tuple[SegmentScore, ...],
    *,
    minimum_separation: float = 0.15,
) -> VerificationReport:
    separation = find_threshold(scores, minimum_separation=minimum_separation)
    return VerificationReport(
        schema_version=SPEAKER_VERIFICATION_SCHEMA_VERSION,
        source_id=source_id,
        distribution=describe(scores),
        separation=separation,
        reliable_count=sum(1 for score in scores if score.is_reliable),
        short_count=sum(1 for score in scores if not score.is_reliable),
    )


def positive_control_rate(
    scores: tuple[SegmentScore, ...],
    threshold: float,
) -> float:
    """Share of segments accepted as the Reference.

    Run against the single-speaker source this should be close to 1. A low value
    means the method rejects the Reference's own voice, which no threshold can
    fix.
    """
    if not scores:
        raise SpeakerVerificationError("no segment scores for the control")
    return sum(1 for score in scores if score.similarity >= threshold) / len(scores)


def public_verification_summary(report: VerificationReport) -> dict[str, Any]:
    distribution = report.distribution
    separation = report.separation
    return {
        "schema_version": report.schema_version,
        "source_id": report.source_id,
        "segment_count": distribution.count,
        "reliable_count": report.reliable_count,
        "short_count": report.short_count,
        "similarity": {
            "mean": round(distribution.mean, 4),
            "median": round(distribution.median, 4),
            "stdev": round(distribution.stdev, 4),
            "min": round(distribution.minimum, 4),
            "max": round(distribution.maximum, 4),
        },
        "separation": {
            "threshold": None
            if separation.threshold is None
            else round(separation.threshold, 4),
            "low_count": separation.low_count,
            "high_count": separation.high_count,
            "low_mean": None
            if separation.low_mean is None
            else round(separation.low_mean, 4),
            "high_mean": None
            if separation.high_mean is None
            else round(separation.high_mean, 4),
            "separation": None
            if separation.separation is None
            else round(separation.separation, 4),
            "is_bimodal": separation.is_bimodal,
            "reason": separation.reason,
        },
    }
