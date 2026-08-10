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


# A transcript cue is not a speaker unit. In an interview one cue routinely
# holds a question and its answer, so scoring the cue whole averages two voices
# into something that matches neither. Windows are cut inside the cue instead.
DEFAULT_WINDOW_SECONDS = 1.5
DEFAULT_HOP_SECONDS = 0.75


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


def iter_windows(
    start_seconds: float,
    end_seconds: float,
    *,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    hop_seconds: float = DEFAULT_HOP_SECONDS,
) -> tuple[tuple[float, float], ...]:
    """Cut a span into overlapping windows.

    Overlap matters: a speaker change landing mid-window would otherwise mix two
    voices in every window that covers it. With a hop of half the window, the
    change is cleanly inside at least one window on each side of it.

    A span shorter than one window yields the whole span, so short cues are still
    scored rather than silently dropped.
    """
    if window_seconds <= 0 or hop_seconds <= 0:
        raise SpeakerVerificationError("window and hop must be positive")
    if end_seconds <= start_seconds:
        raise SpeakerVerificationError("window span must be positive")
    span = end_seconds - start_seconds
    if span <= window_seconds:
        return ((start_seconds, end_seconds),)
    windows: list[tuple[float, float]] = []
    position = start_seconds
    while position + window_seconds <= end_seconds + 1e-9:
        windows.append((position, position + window_seconds))
        position += hop_seconds
    # Keep the tail: without it the last moments of a long cue are never scored.
    if windows and windows[-1][1] < end_seconds - 1e-9:
        windows.append((max(start_seconds, end_seconds - window_seconds), end_seconds))
    return tuple(windows)


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


@dataclass(frozen=True)
class ClusterSplit:
    """Two groups found inside one recording, then labelled by enrolment.

    Absolute similarity to an enrolment voice collapses when the two recordings
    differ acoustically: the whole target drifts down and no threshold separates
    anyone. Clustering inside the target first removes that shift, because it
    lands on both speakers equally. The enrolment is then only used to say which
    of the two groups is the Reference.
    """

    sizes: tuple[int, int]
    centroid_similarities: tuple[float, float]
    reference_index: int
    margin: float
    internal_separation: float
    # Turn-taking leaves long runs of one speaker; noise and music are scattered
    # through the other speaker's audio. A split that is real turn-taking has
    # runs of several windows, not alternation every window.
    mean_run_windows: float
    longest_run_windows: int

    @property
    def is_usable(self) -> bool:
        """Both groups substantial, one clearly closer, and runs long enough.

        The run-length test is what separates two speakers from one speaker
        interrupted by music: single-speaker audio also splits into two clusters,
        so cluster quality alone proves nothing.
        """
        smaller = min(self.sizes)
        return (
            smaller >= 3
            and smaller / sum(self.sizes) >= 0.1
            and self.margin >= 0.05
            and self.internal_separation >= 0.1
            and self.mean_run_windows >= 2.0
        )


def unit(vector: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        raise SpeakerVerificationError("embedding has zero magnitude")
    return tuple(value / norm for value in vector)


def _centroid(vectors: list[tuple[float, ...]]) -> tuple[float, ...]:
    dimension = len(vectors[0])
    return unit(
        tuple(sum(v[i] for v in vectors) / len(vectors) for i in range(dimension))
    )


def two_means(
    embeddings: tuple[tuple[float, ...], ...],
    *,
    iterations: int = 20,
) -> tuple[tuple[int, ...], tuple[tuple[float, ...], tuple[float, ...]]]:
    """Split embeddings into two groups by cosine distance.

    Seeded deterministically from the farthest pair reachable in two passes, so
    the same audio always yields the same split. A random start would make every
    run of the probe disagree with the last one.
    """
    if len(embeddings) < 2:
        raise SpeakerVerificationError("need at least two embeddings to cluster")
    points = [unit(vector) for vector in embeddings]
    first = min(range(len(points)), key=lambda i: cosine_similarity(points[0], points[i]))
    second = min(
        range(len(points)), key=lambda i: cosine_similarity(points[first], points[i])
    )
    centroids = (points[first], points[second])
    labels = tuple(0 for _ in points)
    for _ in range(iterations):
        new_labels = tuple(
            0
            if cosine_similarity(point, centroids[0]) >= cosine_similarity(point, centroids[1])
            else 1
            for point in points
        )
        groups: tuple[list[tuple[float, ...]], list[tuple[float, ...]]] = ([], [])
        for label, point in zip(new_labels, points, strict=True):
            groups[label].append(point)
        if not groups[0] or not groups[1]:
            break
        centroids = (_centroid(groups[0]), _centroid(groups[1]))
        if new_labels == labels:
            labels = new_labels
            break
        labels = new_labels
    return labels, centroids


def run_lengths(labels: tuple[int, ...]) -> tuple[int, ...]:
    """Lengths of consecutive same-label stretches, in order."""
    if not labels:
        raise SpeakerVerificationError("no labels to measure runs on")
    runs: list[int] = [1]
    for previous, current in zip(labels, labels[1:], strict=False):
        if current == previous:
            runs[-1] += 1
        else:
            runs.append(1)
    return tuple(runs)


def label_clusters(
    embeddings: tuple[tuple[float, ...], ...],
    enrolment: tuple[float, ...],
) -> ClusterSplit:
    labels, centroids = two_means(embeddings)
    runs = run_lengths(labels)
    sizes = (sum(1 for label in labels if label == 0), sum(1 for label in labels if label == 1))
    if not all(sizes):
        raise SpeakerVerificationError("clustering collapsed into a single group")
    similarities = (
        cosine_similarity(centroids[0], enrolment),
        cosine_similarity(centroids[1], enrolment),
    )
    reference_index = 0 if similarities[0] >= similarities[1] else 1
    return ClusterSplit(
        sizes=sizes,
        centroid_similarities=similarities,
        reference_index=reference_index,
        margin=abs(similarities[0] - similarities[1]),
        internal_separation=1.0 - cosine_similarity(centroids[0], centroids[1]),
        mean_run_windows=sum(runs) / len(runs),
        longest_run_windows=max(runs),
    )


def public_cluster_summary(split: ClusterSplit) -> dict[str, Any]:
    return {
        "sizes": list(split.sizes),
        "centroid_similarity_to_enrolment": [
            round(value, 4) for value in split.centroid_similarities
        ],
        "reference_cluster": split.reference_index,
        "reference_share": round(
            split.sizes[split.reference_index] / sum(split.sizes), 4
        ),
        "margin": round(split.margin, 4),
        "internal_separation": round(split.internal_separation, 4),
        "mean_run_windows": round(split.mean_run_windows, 2),
        "longest_run_windows": split.longest_run_windows,
        "is_usable": split.is_usable,
    }
