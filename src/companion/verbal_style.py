"""Interface-independent wording policy, loaded from a measured profile.

The previous policy was three hand-written English instructions with no evidence
behind them. Style now comes from ``configs/verbal_style/*.py`` so that what the
companion sounds like is data a person can inspect, diff and swap — and so the
Reference-derived profile can be compared against the old one on the same input.

A profile that is missing or malformed raises. There is no fallback to a default
style: silently answering in the wrong register is worse than refusing to start.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import random
from typing import Any

from companion.response import VerbalStylePlan

DEFAULT_PROFILE = "reference_broadcast"
ALLOWED_PROFILES = ("base", "reference_broadcast")

# Plain speech and polite speech are not interchangeable in Korean; picking the
# wrong one is the most visible way to sound like someone else.
REGISTER_INSTRUCTIONS = {
    # Measured registers are mixtures, not switches. An absolute rule produced
    # 0% polite endings where the Reference had 26%, which scored further away
    # than allowing the mixture.
    "mostly_plain": "한국어로 주로 반말을 쓰되 가끔 존댓말도 섞어. 격식체는 쓰지 마.",
    "plain": "한국어 반말로 말해. 존댓말과 격식체는 쓰지 마.",
    "polite_casual": "한국어 존댓말로 말해. 격식체는 쓰지 마.",
    "polite_formal": "한국어 격식체로 말해.",
}
ALLOWED_HESITATION_USAGE = ("never", "sparing")


class VerbalStyleError(ValueError):
    """Raised when a style profile cannot be used without guessing."""


@dataclass(frozen=True)
class TurnStyle:
    """One turn's style, decided once.

    ``plan`` and ``instruction`` must agree. When the register is sampled per
    turn, deciding them in two separate calls would roll the dice twice and the
    companion could be told to use plain speech while the response metadata says
    polite.
    """

    plan: VerbalStylePlan
    instruction: str | None


@dataclass(frozen=True)
class VerbalStyleProfile:
    name: str
    register: str
    # Measured share of polite endings. A model treats "mostly plain" as an
    # absolute rule and never produces the minority register, so the mixture is
    # produced here instead: each turn draws one register and is instructed
    # absolutely. None keeps the register fixed.
    polite_ratio: float | None
    max_sentences: int
    # Sentence count alone did not control length: generated utterances ran
    # roughly twice the Reference's word count. A word target does.
    max_words_per_sentence: int | None
    # False reproduces the pre-Reference behaviour, where ordinary turns carried
    # no style instruction at all. Kept exact so the two can be compared.
    shared_instruction: bool
    discourse_markers: tuple[str, ...]
    hesitation_markers: tuple[str, ...]
    hesitation_usage: str
    acts: dict[str, dict[str, Any]]


def load_verbal_style(profile: str = DEFAULT_PROFILE) -> VerbalStyleProfile:
    if profile not in ALLOWED_PROFILES:
        raise VerbalStyleError(f"unsupported verbal style profile: {profile}")
    try:
        raw: dict[str, Any] = import_module(f"configs.verbal_style.{profile}").VERBAL_STYLE
    except (ImportError, AttributeError) as error:
        raise VerbalStyleError(f"could not load verbal style profile {profile}") from error
    return _parse(profile, raw)


class VerbalStylePlanner:
    def __init__(
        self,
        profile: VerbalStyleProfile | None = None,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._profile = profile or load_verbal_style()
        self._rng = rng or random.Random()

    @property
    def profile(self) -> VerbalStyleProfile:
        return self._profile

    def plan_turn(self, dialogue_act: str) -> TurnStyle:
        """Decide this turn's register once, then derive both outputs from it."""
        register = self._draw_register()
        return TurnStyle(
            plan=self._plan(dialogue_act, register),
            instruction=self._instruction(dialogue_act, register),
        )

    def plan(self, dialogue_act: str) -> VerbalStylePlan:
        """Inspection only. Use ``plan_turn`` for a turn: this draws its own."""
        return self._plan(dialogue_act, self._draw_register())

    def _draw_register(self) -> str:
        if self._profile.polite_ratio is None:
            return self._profile.register
        return (
            "polite_casual"
            if self._rng.random() < self._profile.polite_ratio
            else "plain"
        )

    def _plan(self, dialogue_act: str, register: str) -> VerbalStylePlan:
        act = self._act(dialogue_act)
        return VerbalStylePlan(
            tone=act["tone"],
            directness=act["directness"],
            sentence_length=act["sentence_length"],
            register=register,
        )

    def instruction(self, dialogue_act: str) -> str | None:
        """Inspection only. Use ``plan_turn`` for a turn: this draws its own."""
        return self._instruction(dialogue_act, self._draw_register())

    def _instruction(self, dialogue_act: str, register: str) -> str | None:
        """Build the system instruction, or None when the profile is silent.

        The register line is always first: it is the constraint the model is
        most likely to drop when later instructions compete for attention.
        """
        parts: list[str] = []
        if self._profile.shared_instruction:
            parts.append(REGISTER_INSTRUCTIONS[register])
            length = f"한 번에 {self._profile.max_sentences}문장 이내로 말해."
            if self._profile.max_words_per_sentence:
                length += f" 한 문장은 {self._profile.max_words_per_sentence}단어를 넘기지 마."
            parts.append(length)
            if self._profile.discourse_markers:
                markers = ", ".join(self._profile.discourse_markers)
                parts.append(f"화제를 바꿀 때는 {markers} 같은 말을 자연스럽게 써.")
            if self._profile.hesitation_usage == "sparing" and self._profile.hesitation_markers:
                markers = ", ".join(self._profile.hesitation_markers)
                parts.append(
                    f"{markers} 같은 말은 아주 드물게만 써. 대부분의 문장에는 넣지 마."
                )
        act_instruction = self._act(dialogue_act).get("instruction")
        if act_instruction:
            parts.append(act_instruction)
        return " ".join(parts) if parts else None

    def _act(self, dialogue_act: str) -> dict[str, Any]:
        return self._profile.acts.get(dialogue_act, self._profile.acts["default"])


def _parse(profile: str, raw: dict[str, Any]) -> VerbalStyleProfile:
    if not isinstance(raw, dict):
        raise VerbalStyleError(f"verbal style profile {profile} must be a mapping")
    register = raw.get("register")
    if register not in REGISTER_INSTRUCTIONS:
        raise VerbalStyleError(f"unsupported register in profile {profile}: {register!r}")
    usage = raw.get("hesitation_usage")
    if usage not in ALLOWED_HESITATION_USAGE:
        raise VerbalStyleError(f"unsupported hesitation usage in profile {profile}: {usage!r}")
    max_sentences = raw.get("max_sentences")
    if not isinstance(max_sentences, int) or isinstance(max_sentences, bool) or max_sentences < 1:
        raise VerbalStyleError(f"max_sentences in profile {profile} must be a positive integer")
    max_words = raw.get("max_words_per_sentence")
    if max_words is not None and (
        not isinstance(max_words, int) or isinstance(max_words, bool) or max_words < 1
    ):
        raise VerbalStyleError(
            f"max_words_per_sentence in profile {profile} must be a positive integer or None"
        )
    acts = raw.get("acts")
    if not isinstance(acts, dict) or "default" not in acts:
        raise VerbalStyleError(f"profile {profile} must define acts including 'default'")
    for name, act in acts.items():
        _validate_act(profile, name, act)
    shared = raw.get("shared_instruction", True)
    if not isinstance(shared, bool):
        raise VerbalStyleError(f"shared_instruction in profile {profile} must be a boolean")
    polite_ratio = raw.get("polite_ratio")
    if polite_ratio is not None and (
        isinstance(polite_ratio, bool)
        or not isinstance(polite_ratio, (int, float))
        or not 0.0 <= float(polite_ratio) <= 1.0
    ):
        raise VerbalStyleError(f"polite_ratio in profile {profile} must be 0..1 or None")
    return VerbalStyleProfile(
        name=str(raw.get("name") or profile),
        register=register,
        polite_ratio=None if polite_ratio is None else float(polite_ratio),
        max_sentences=max_sentences,
        max_words_per_sentence=max_words,
        shared_instruction=shared,
        discourse_markers=_strings(profile, raw.get("discourse_markers", ())),
        hesitation_markers=_strings(profile, raw.get("hesitation_markers", ())),
        hesitation_usage=usage,
        acts=acts,
    )


def _validate_act(profile: str, name: str, act: Any) -> None:
    if not isinstance(act, dict):
        raise VerbalStyleError(f"act {name} in profile {profile} must be a mapping")
    for field in ("tone", "sentence_length"):
        if not isinstance(act.get(field), str) or not act[field]:
            raise VerbalStyleError(f"act {name} in profile {profile} needs a {field}")
    directness = act.get("directness")
    if isinstance(directness, bool) or not isinstance(directness, (int, float)):
        raise VerbalStyleError(f"act {name} in profile {profile} needs a numeric directness")
    if not 0.0 <= float(directness) <= 1.0:
        raise VerbalStyleError(f"directness in act {name} of profile {profile} must be 0..1")
    instruction = act.get("instruction")
    if instruction is not None and (not isinstance(instruction, str) or not instruction.strip()):
        raise VerbalStyleError(f"act {name} in profile {profile} has an empty instruction")


def _strings(profile: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise VerbalStyleError(f"profile {profile} has an invalid marker list")
    return tuple(value)
