from dataclasses import replace

from companion.belief_reflection import BeliefSafetyReview
from companion.belief_reflection_evaluation import (
    LOCKED_V1_SCENES,
    EvaluatedBeliefSafety,
    activation_gates,
    render_markdown,
)


def _review(accept: bool) -> BeliefSafetyReview:
    return BeliefSafetyReview(
        winter_owned=accept,
        low_risk=accept,
        evidence_entails_stance=accept,
        no_user_fact_claim=accept,
        no_identity_change=accept,
        no_capability_claim=accept,
        no_dependency_or_coercion=accept,
        counterevidence_considered=accept,
        accept=accept,
    )


def test_locked_v1_has_twenty_scenes_and_all_required_risk_classes() -> None:
    assert len(LOCKED_V1_SCENES) == 20
    assert {scene.risk_class for scene in LOCKED_V1_SCENES} == {
        "safe",
        "user_fact",
        "identity",
        "capability",
        "dependency",
        "not_entailed",
        "counterevidence",
    }


def test_expected_reviews_pass_all_activation_gates() -> None:
    results = tuple(
        EvaluatedBeliefSafety(
            scene,
            _review(scene.expected_accept),
            None,
            0.1,
        )
        for scene in LOCKED_V1_SCENES
    )

    gates = activation_gates(results)

    assert all(gate.passed for gate in gates)
    assert "PASS `protected_false_accepts`" in render_markdown(results, gates)


def test_one_unsafe_accept_fails_false_positive_gates() -> None:
    results = [
        EvaluatedBeliefSafety(
            scene,
            _review(scene.expected_accept),
            None,
            0.1,
        )
        for scene in LOCKED_V1_SCENES
    ]
    unsafe_index = next(
        index
        for index, result in enumerate(results)
        if result.scene.risk_class == "capability"
    )
    results[unsafe_index] = replace(results[unsafe_index], review=_review(True))

    gates = activation_gates(tuple(results))

    failed = {gate.name for gate in gates if not gate.passed}
    assert failed == {"protected_false_accepts", "all_unsafe_false_accepts"}
