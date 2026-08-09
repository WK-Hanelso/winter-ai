"""Offline coverage for the measured verbal style profile."""

from __future__ import annotations

import pytest

from companion.verbal_style import (
    DEFAULT_PROFILE,
    REGISTER_INSTRUCTIONS,
    VerbalStyleError,
    VerbalStylePlanner,
    VerbalStyleProfile,
    load_verbal_style,
)


def _profile(**overrides: object) -> VerbalStyleProfile:
    defaults = {
        "name": "test",
        "register": "plain",
        "max_sentences": 2,
        "max_words_per_sentence": 8,
        "shared_instruction": True,
        "discourse_markers": ("근데",),
        "hesitation_markers": ("뭔가",),
        "hesitation_usage": "sparing",
        "acts": {
            "default": {
                "tone": "neutral",
                "directness": 0.7,
                "sentence_length": "short",
                "instruction": None,
            },
            "warning": {
                "tone": "serious",
                "directness": 0.9,
                "sentence_length": "short",
                "instruction": "중요한 점 먼저.",
            },
        },
    }
    defaults.update(overrides)
    return VerbalStyleProfile(**defaults)  # type: ignore[arg-type]


def test_reference_profile_is_the_default_and_uses_plain_speech() -> None:
    profile = load_verbal_style()

    assert DEFAULT_PROFILE == "reference_broadcast"
    # Measured politeness ratio was 0.31: plain dominates but polite still
    # appears, so the register is a mixture rather than an absolute rule.
    assert profile.register == "mostly_plain"
    assert profile.max_sentences == 1
    assert profile.max_words_per_sentence == 8


def test_hand_written_profile_is_still_loadable_for_comparison() -> None:
    profile = load_verbal_style("base")

    assert profile.register == "polite_casual"
    assert profile.hesitation_usage == "never"


def test_hand_written_profile_reproduces_the_pre_reference_behaviour() -> None:
    # It shipped with no instruction on ordinary turns; the comparison has to be
    # against that, not against a tidied version of it.
    planner = VerbalStylePlanner(load_verbal_style("base"))

    assert planner.instruction("answer") is None
    assert planner.instruction("memory_candidate") == (
        "Respond briefly and warmly in Korean. Do not pressure the user."
    )
    assert planner.instruction("warning") == (
        "Respond briefly and clearly in Korean. State the important point first."
    )
    assert planner.plan("answer").sentence_length == "normal"


def test_unknown_profile_raises_instead_of_falling_back() -> None:
    with pytest.raises(VerbalStyleError, match="unsupported verbal style profile"):
        load_verbal_style("does-not-exist")


def test_instruction_states_the_register_first() -> None:
    instruction = VerbalStylePlanner(_profile()).instruction("answer")

    assert instruction is not None
    assert instruction.startswith(REGISTER_INSTRUCTIONS["plain"])


def test_instruction_states_a_word_target_when_the_profile_has_one() -> None:
    # Sentence count alone did not control length in measurement.
    with_words = VerbalStylePlanner(_profile()).instruction("answer")
    without_words = VerbalStylePlanner(
        _profile(max_words_per_sentence=None)
    ).instruction("answer")

    assert with_words is not None and "8단어" in with_words
    assert without_words is not None and "단어를 넘기지" not in without_words


def test_instruction_mentions_discourse_and_hesitation_markers() -> None:
    instruction = VerbalStylePlanner(_profile()).instruction("answer")

    assert instruction is not None
    assert "근데" in instruction
    assert "뭔가" in instruction


def test_hesitation_markers_are_omitted_when_usage_is_never() -> None:
    planner = VerbalStylePlanner(_profile(hesitation_usage="never"))

    instruction = planner.instruction("answer")

    assert instruction is not None
    assert "뭔가" not in instruction


def test_act_instruction_is_appended_after_the_shared_style() -> None:
    instruction = VerbalStylePlanner(_profile()).instruction("warning")

    assert instruction is not None
    assert instruction.endswith("중요한 점 먼저.")


def test_unknown_act_falls_back_to_the_default_act_not_to_silence() -> None:
    planner = VerbalStylePlanner(_profile())

    assert planner.plan("unheard-of-act").tone == "neutral"
    assert planner.instruction("unheard-of-act") is not None


def test_plan_carries_the_register() -> None:
    assert VerbalStylePlanner(_profile()).plan("answer").register == "plain"


def test_malformed_profiles_are_refused() -> None:
    from companion.verbal_style import _parse

    base = {
        "register": "plain",
        "max_sentences": 2,
        "hesitation_usage": "sparing",
        "acts": {"default": {"tone": "a", "directness": 0.5, "sentence_length": "short"}},
    }

    with pytest.raises(VerbalStyleError, match="unsupported register"):
        _parse("p", {**base, "register": "shouting"})
    with pytest.raises(VerbalStyleError, match="unsupported hesitation usage"):
        _parse("p", {**base, "hesitation_usage": "always"})
    with pytest.raises(VerbalStyleError, match="max_sentences"):
        _parse("p", {**base, "max_sentences": 0})
    with pytest.raises(VerbalStyleError, match="max_words_per_sentence"):
        _parse("p", {**base, "max_words_per_sentence": 0})
    with pytest.raises(VerbalStyleError, match="must define acts"):
        _parse("p", {**base, "acts": {"warning": {}}})
    with pytest.raises(VerbalStyleError, match="numeric directness"):
        _parse("p", {**base, "acts": {"default": {"tone": "a", "sentence_length": "s"}}})
    with pytest.raises(VerbalStyleError, match="must be 0..1"):
        _parse(
            "p",
            {**base, "acts": {"default": {"tone": "a", "directness": 2.0, "sentence_length": "s"}}},
        )
    with pytest.raises(VerbalStyleError, match="invalid marker list"):
        _parse("p", {**base, "discourse_markers": ("", )})
