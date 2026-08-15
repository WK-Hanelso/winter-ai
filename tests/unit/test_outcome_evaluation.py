import pytest

from companion.outcome import OutcomeAssessment
from companion.outcome_evaluation import (
    EvaluatedOutcome,
    OutcomeExpectation,
    activation_gates,
    default_calibration_scenes,
    default_held_out_scenes,
    evaluate,
    locked_v2_scenes,
    render_markdown,
)


def _assessment(
    text: str,
    *,
    outcome: str = "accepted",
    intent_match: str = "confirmed",
    usefulness: str = "helpful",
    memory_confirmation: bool = False,
    memory_contradiction: bool = False,
    improvement_confirmation: bool = False,
) -> OutcomeAssessment:
    confirmation_signal = (
        [{"content": "기억 확인", "confidence": 0.9, "evidence": [text]}]
        if memory_confirmation
        else []
    )
    contradiction_signal = (
        [{"content": "기억 반박", "confidence": 0.9, "evidence": [text]}]
        if memory_contradiction
        else []
    )
    improvement_signal = (
        [{"content": "응답 실패", "confidence": 0.9, "evidence": [text]}]
        if improvement_confirmation
        else []
    )
    return OutcomeAssessment.from_dict(
        {
            "schema_version": 1,
            "outcome": outcome,
            "confidence": 0.9,
            "evidence": [text],
            "intent_match": intent_match,
            "response_usefulness": usefulness,
            "affect_shift": "unclear",
            "memory_confirmation": confirmation_signal,
            "memory_contradiction": contradiction_signal,
            "improvement_confirmation": improvement_signal,
            "uncertainties": [],
        },
        evidence_text=text,
    )


def test_default_set_covers_outcomes_ambiguity_memory_and_affect() -> None:
    scenes = default_held_out_scenes()
    calibration = default_calibration_scenes()
    locked_v2 = locked_v2_scenes()

    assert len(scenes) == 15
    assert len(calibration) == 15
    assert len(locked_v2) == 15
    assert len({scene.label for scene in scenes}) == 15
    assert {scene.label for scene in scenes}.isdisjoint(
        scene.label for scene in calibration
    )
    assert {scene.next_user_text for scene in scenes}.isdisjoint(
        scene.next_user_text for scene in calibration
    )
    all_labels = [
        scene.label for suite in (scenes, calibration, locked_v2) for scene in suite
    ]
    assert len(all_labels) == len(set(all_labels))
    assert {scene.next_user_text for scene in locked_v2}.isdisjoint(
        scene.next_user_text for suite in (scenes, calibration) for scene in suite
    )
    expected_outcomes = set().union(
        *(scene.expectation.allowed_outcomes for scene in scenes)
    )
    required_outcomes = {
        "accepted",
        "corrected",
        "rejected",
        "continued",
        "abandoned",
        "unclear",
    }
    assert required_outcomes <= expected_outcomes
    assert sum("ambiguous" in scene.expectation.tags for scene in scenes) >= 3
    assert any(
        "memory_confirmation" in scene.expectation.required_signals
        for scene in scenes
    )
    assert any(scene.expectation.allowed_affect_shifts for scene in scenes)


def test_expectation_rejects_unknown_or_overlapping_signal_contract() -> None:
    with pytest.raises(ValueError, match="unsupported signal"):
        OutcomeExpectation(
            frozenset({"accepted"}),
            required_signals=frozenset({"made_up"}),
        )
    with pytest.raises(ValueError, match="required and forbidden"):
        OutcomeExpectation(
            frozenset({"accepted"}),
            required_signals=frozenset({"memory_confirmation"}),
        )


def test_evaluate_exposes_wrong_outcome_and_forbidden_memory_signal() -> None:
    scene = next(
        item
        for item in default_held_out_scenes()
        if item.label == "locked_topic_change"
    )
    result = EvaluatedOutcome(
        scene,
        _assessment(scene.next_user_text, memory_confirmation=True),
        None,
        0.5,
    )

    checks = evaluate((result,))

    failed = {check.name for check in checks if not check.passed}
    assert "outcome" in failed
    assert "forbidden memory_confirmation" in failed


def test_activation_gates_measure_safety_precision_and_repetition() -> None:
    scenes = default_held_out_scenes()
    results = tuple(
        EvaluatedOutcome(
            scene,
            _assessment(
                scene.next_user_text,
                outcome=next(iter(scene.expectation.allowed_outcomes)),
                intent_match=(
                    "contradicted"
                    if "correction" in scene.expectation.tags
                    else next(
                        iter(scene.expectation.allowed_intent_matches), "unclear"
                    )
                ),
                usefulness=next(
                    iter(scene.expectation.allowed_usefulness), "unclear"
                ),
                memory_confirmation=(
                    "memory_confirmation_positive" in scene.expectation.tags
                ),
                memory_contradiction=(
                    "memory_contradiction_positive" in scene.expectation.tags
                ),
                improvement_confirmation=(
                    "improvement_confirmation_positive"
                    in scene.expectation.tags
                ),
            ),
            None,
            0.2,
            repeat,
        )
        for repeat in (1, 2)
        for scene in scenes
    )

    gates = activation_gates(results)

    assert all(gate.passed for gate in gates)
    assert "7/7 activation gates passed" in render_markdown(
        results, evaluate(results), gates
    )


def test_activation_gate_fails_false_acceptance_and_single_run() -> None:
    scene = next(
        item
        for item in default_held_out_scenes()
        if item.label == "locked_minimal_ambiguous"
    )
    result = EvaluatedOutcome(
        scene, _assessment(scene.next_user_text), None, 0.1
    )

    gates = activation_gates((result,))

    failed = {gate.name for gate in gates if not gate.passed}
    assert "ambiguous/topic-shift false acceptance = 0" in failed
    assert "deterministic repeated outcomes" in failed
