"""Extract measurable speech-style traits from a Reference transcript.

``VerbalStylePlanner`` currently encodes style as three hand-written English
instructions. Replacing that with something grounded needs numbers, not
impressions: which sentence endings this person actually uses, how often they
hesitate, how long their utterances run, how often they restart a phrase.

Two constraints shape the implementation:

* **No morphological analyser.** Korean sentence endings are approximated from
  surface patterns. A tagger would be more precise but adds a heavyweight
  dependency to a probe whose job is to inform a design decision.
* **No transcript text leaves this module.** Counts and ratios are safe to
  print; utterances are not. ``public_style_summary`` carries no verbatim
  speech, and example selection is a separate, explicit call.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any

from companion.reference_subtitle_probe import FILLER_TOKENS, SubtitleCue

SPEECH_STYLE_SCHEMA_VERSION = 1

# Surface forms, longest first: "습니다" must win before "다" is considered.
# Politeness is what the companion has to get right first, so endings are
# grouped by register rather than listed flat.
POLITE_FORMAL_ENDINGS = ("습니다", "ㅂ니다", "습니까", "십시오", "ㅂ시다")
POLITE_CASUAL_ENDINGS = ("어요", "아요", "에요", "예요", "이에요", "세요", "네요",
                         "지요", "죠", "요")
PLAIN_ENDINGS = ("는다", "ㄴ다", "구나", "네", "지", "야", "어", "아", "다")

_SENTENCE_BREAK = re.compile(r"[.?!…]+")
_HANGUL_WORD = re.compile(r"[가-힣]+")
_TRAILING_PUNCTUATION = re.compile(r"[^가-힣A-Za-z0-9]+$")


class ReferenceSpeechStyleError(RuntimeError):
    """Raised when a transcript cannot be characterised honestly."""


@dataclass(frozen=True)
class EndingProfile:
    """How this speaker closes an utterance, by register."""

    polite_formal: int
    polite_casual: int
    plain: int
    unclassified: int

    @property
    def classified(self) -> int:
        return self.polite_formal + self.polite_casual + self.plain

    @property
    def polite_ratio(self) -> float | None:
        """Share of classified endings that are polite, formal or casual."""
        if not self.classified:
            return None
        return (self.polite_formal + self.polite_casual) / self.classified


@dataclass(frozen=True)
class SpeechStyleProfile:
    schema_version: int
    label: str
    utterance_count: int
    word_count: int
    character_count: int
    mean_words_per_utterance: float
    median_words_per_utterance: float
    endings: EndingProfile
    filler_hits: int
    filler_per_100_words: float
    top_fillers: tuple[tuple[str, int], ...]
    immediate_repetition_count: int
    repetition_per_100_words: float
    top_words: tuple[tuple[str, int], ...]


def profile_transcript(
    label: str,
    cues: tuple[SubtitleCue, ...],
    *,
    top_n: int = 10,
    minimum_word_length: int = 2,
) -> SpeechStyleProfile:
    """Measure one transcript's style traits.

    Utterances are split on sentence punctuation where present and fall back to
    cue boundaries otherwise. Both forms occur in practice: whisper punctuates,
    older automatic captions do not.
    """
    if not cues:
        raise ReferenceSpeechStyleError("transcript has no cues to profile")
    if top_n < 1:
        raise ReferenceSpeechStyleError("top_n must be at least 1")

    utterances = _utterances(cues)
    if not utterances:
        raise ReferenceSpeechStyleError("transcript has no usable utterances")

    words = [word for utterance in utterances for word in _HANGUL_WORD.findall(utterance)]
    if not words:
        raise ReferenceSpeechStyleError("transcript has no Korean words to profile")

    lengths = sorted(len(_HANGUL_WORD.findall(utterance)) for utterance in utterances)
    filler_counter = Counter(word for word in words if word in FILLER_TOKENS)
    content_counter = Counter(
        word
        for word in words
        if len(word) >= minimum_word_length and word not in FILLER_TOKENS
    )
    fillers = sum(filler_counter.values())
    repetitions = _immediate_repetitions(utterances)

    return SpeechStyleProfile(
        schema_version=SPEECH_STYLE_SCHEMA_VERSION,
        label=label,
        utterance_count=len(utterances),
        word_count=len(words),
        character_count=sum(len(word) for word in words),
        mean_words_per_utterance=len(words) / len(utterances),
        median_words_per_utterance=_median(lengths),
        endings=_ending_profile(utterances),
        filler_hits=fillers,
        filler_per_100_words=fillers * 100 / len(words),
        top_fillers=tuple(filler_counter.most_common(top_n)),
        immediate_repetition_count=repetitions,
        repetition_per_100_words=repetitions * 100 / len(words),
        top_words=tuple(content_counter.most_common(top_n)),
    )


def public_style_summary(profile: SpeechStyleProfile) -> dict[str, Any]:
    """Return counts and ratios only.

    Individual words appear here because a word frequency list is the point of
    the measurement; whole utterances never do.
    """
    return {
        "schema_version": profile.schema_version,
        "label": profile.label,
        "utterance_count": profile.utterance_count,
        "word_count": profile.word_count,
        "character_count": profile.character_count,
        "mean_words_per_utterance": round(profile.mean_words_per_utterance, 2),
        "median_words_per_utterance": profile.median_words_per_utterance,
        "endings": {
            "polite_formal": profile.endings.polite_formal,
            "polite_casual": profile.endings.polite_casual,
            "plain": profile.endings.plain,
            "unclassified": profile.endings.unclassified,
            "polite_ratio": (
                None
                if profile.endings.polite_ratio is None
                else round(profile.endings.polite_ratio, 3)
            ),
        },
        "filler_hits": profile.filler_hits,
        "filler_per_100_words": round(profile.filler_per_100_words, 2),
        "top_fillers": [
            {"token": token, "count": count} for token, count in profile.top_fillers
        ],
        "immediate_repetition_count": profile.immediate_repetition_count,
        "repetition_per_100_words": round(profile.repetition_per_100_words, 2),
        "top_words": [
            {"word": word, "count": count} for word, count in profile.top_words
        ],
    }


def compare_profiles(
    primary: SpeechStyleProfile,
    secondary: SpeechStyleProfile,
) -> dict[str, Any]:
    """Report where two transcripts of the same audio disagree.

    Traits that survive both transcriptions are the ones worth designing
    against; traits that appear in only one are artefacts of a tool.
    """
    return {
        "primary": primary.label,
        "secondary": secondary.label,
        "word_count_ratio": _ratio(primary.word_count, secondary.word_count),
        "mean_words_ratio": _ratio(
            primary.mean_words_per_utterance, secondary.mean_words_per_utterance
        ),
        "filler_rate_ratio": _ratio(
            primary.filler_per_100_words, secondary.filler_per_100_words
        ),
        "polite_ratio_difference": (
            None
            if primary.endings.polite_ratio is None
            or secondary.endings.polite_ratio is None
            else round(primary.endings.polite_ratio - secondary.endings.polite_ratio, 3)
        ),
        "shared_top_words": sorted(
            {word for word, _ in primary.top_words}
            & {word for word, _ in secondary.top_words}
        ),
    }


def _utterances(cues: tuple[SubtitleCue, ...]) -> list[str]:
    utterances: list[str] = []
    for cue in cues:
        parts = [part.strip() for part in _SENTENCE_BREAK.split(cue.text)]
        utterances.extend(part for part in parts if part)
    return utterances


def _ending_profile(utterances: list[str]) -> EndingProfile:
    counts = {"polite_formal": 0, "polite_casual": 0, "plain": 0, "unclassified": 0}
    for utterance in utterances:
        counts[_classify_ending(utterance)] += 1
    return EndingProfile(
        polite_formal=counts["polite_formal"],
        polite_casual=counts["polite_casual"],
        plain=counts["plain"],
        unclassified=counts["unclassified"],
    )


def _classify_ending(utterance: str) -> str:
    stripped = _TRAILING_PUNCTUATION.sub("", utterance)
    if not stripped:
        return "unclassified"
    # Formal beats casual beats plain: "습니다" also ends with "다".
    for group, name in (
        (POLITE_FORMAL_ENDINGS, "polite_formal"),
        (POLITE_CASUAL_ENDINGS, "polite_casual"),
        (PLAIN_ENDINGS, "plain"),
    ):
        if any(stripped.endswith(ending) for ending in group):
            return name
    return "unclassified"


def _immediate_repetitions(utterances: list[str]) -> int:
    """Count words repeated back-to-back inside one utterance.

    Only within an utterance: a word repeated across a boundary is more likely
    a transcription seam than a spoken repair.
    """
    total = 0
    for utterance in utterances:
        words = _HANGUL_WORD.findall(utterance)
        total += sum(
            1
            for previous, current in zip(words, words[1:], strict=False)
            if previous == current
        )
    return total


def _median(sorted_values: list[int]) -> float:
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return float(sorted_values[middle])
    return (sorted_values[middle - 1] + sorted_values[middle]) / 2


def _ratio(primary: float, secondary: float) -> float | None:
    if not secondary:
        return None
    return round(primary / secondary, 3)
