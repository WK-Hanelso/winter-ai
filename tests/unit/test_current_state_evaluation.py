import sys

from experiments.current_state_eval import main
import pytest

from companion.current_state_evaluation import (
    CurrentStateHeldOutScene,
    EvaluatedStateExtraction,
    ModelStageTrace,
    default_held_out_scenes,
    evaluate,
    locked_held_out_scenes_v1,
    render_markdown,
    summarize,
)
from companion.reflection import CurrentStateProposal, ReflectionExtraction


def test_default_state_set_covers_positive_context_and_safety_scenes() -> None:
    scenes = default_held_out_scenes()

    assert len(scenes) == 14
    assert len({scene.label for scene in scenes}) == 14
    assert sum(bool(scene.expected_updates) for scene in scenes) == 6
    assert sum(not scene.expected_updates for scene in scenes) == 8
    assert any(scene.context for scene in scenes)
    assert any(len(scene.expected_updates) == 2 for scene in scenes)


def test_locked_v1_is_distinct_and_covers_new_positive_and_safety_scenes() -> None:
    development = default_held_out_scenes()
    locked = locked_held_out_scenes_v1()

    assert len(locked) == 12
    assert {scene.label for scene in development}.isdisjoint(
        scene.label for scene in locked
    )
    assert sum(bool(scene.expected_updates) for scene in locked) == 6
    assert sum(not scene.expected_updates for scene in locked) == 6
    assert any(scene.context for scene in locked)
    assert any(len(scene.expected_updates) == 2 for scene in locked)

    results = tuple(
        EvaluatedStateExtraction(scene, _expected_extraction(scene), None, 0.2)
        for scene in locked
    )
    checks = evaluate(results)
    summary = summarize(results, checks)

    assert summary.full_suite
    assert summary.candidate_audit_gate_passed


def test_perfect_state_results_pass_candidate_audit_gate() -> None:
    results = tuple(
        EvaluatedStateExtraction(
            scene,
            _expected_extraction(scene),
            None,
            0.5,
        )
        for scene in default_held_out_scenes()
    )

    checks = evaluate(results)
    summary = summarize(results, checks)

    assert all(check.passed for check in checks)
    assert summary.passing_scenes == 14
    assert summary.precision == 1.0
    assert summary.recall == 1.0
    assert summary.topic_precision == 1.0
    assert summary.topic_recall == 1.0
    assert summary.full_suite
    assert summary.negative_safety_passed
    assert summary.candidate_audit_gate_passed
    report = render_markdown(results, checks, summary)
    assert "14/14 scenes passed" in report
    assert "never permits automatic Memory activation" in report


def test_false_positive_and_missed_change_fail_safety_gate() -> None:
    scenes = default_held_out_scenes()
    results: list[EvaluatedStateExtraction] = []
    for scene in scenes:
        extraction = _expected_extraction(scene)
        if scene.label == "wellbeing_improved":
            extraction = ReflectionExtraction(())
        elif scene.label == "question_not_update":
            snapshot = scene.current_states[0]
            extraction = ReflectionExtraction(
                (),
                (
                    CurrentStateProposal(
                        snapshot.topic,
                        scene.user_text,
                        0.8,
                        (scene.user_text,),
                        snapshot.memory_id,
                    ),
                ),
            )
        results.append(EvaluatedStateExtraction(scene, extraction, None, 0.5))

    result_tuple = tuple(results)
    checks = evaluate(result_tuple)
    summary = summarize(result_tuple, checks)

    assert summary.false_positives == 1
    assert summary.false_negatives == 1
    assert summary.topic_false_positives == 1
    assert summary.topic_false_negatives == 1
    assert summary.unsafe_negative_updates == 1
    assert not summary.negative_safety_passed
    assert not summary.candidate_audit_gate_passed
    failed_scenes = {check.scene for check in checks if not check.passed}
    assert failed_scenes == {"wellbeing_improved", "question_not_update"}


def test_selected_scene_is_diagnostic_and_cannot_pass_full_gate() -> None:
    scene = default_held_out_scenes()[0]
    results = (
        EvaluatedStateExtraction(scene, _expected_extraction(scene), None, 0.2),
    )

    checks = evaluate(results)
    summary = summarize(results, checks)

    assert all(check.passed for check in checks)
    assert not summary.full_suite
    assert not summary.candidate_audit_gate_passed


def test_ignored_candidate_warning_fails_full_audit_gate() -> None:
    scenes = default_held_out_scenes()
    results = tuple(
        EvaluatedStateExtraction(
            scene,
            (
                ReflectionExtraction(
                    _expected_extraction(scene).proposals,
                    _expected_extraction(scene).current_state_updates,
                    ("current-state update 2 ignored: confidence is invalid",),
                )
                if index == 0
                else _expected_extraction(scene)
            ),
            None,
            0.5,
        )
        for index, scene in enumerate(scenes)
    )

    checks = evaluate(results)
    summary = summarize(results, checks)

    assert summary.candidate_warnings == 1
    assert not summary.candidate_audit_gate_passed


def test_evaluator_rejects_wrong_previous_memory_link() -> None:
    scene = default_held_out_scenes()[0]
    expected = scene.expected_updates[0]
    extraction = ReflectionExtraction(
        (),
        (
            CurrentStateProposal(
                expected.topic,
                expected.evidence,
                0.9,
                (expected.evidence,),
                "wrong-memory",
            ),
        ),
    )

    checks = evaluate((EvaluatedStateExtraction(scene, extraction, None, 0.2),))

    failed = {check.name for check in checks if not check.passed}
    assert failed == {"active-state link"}


def test_safe_full_sentence_evidence_can_be_an_accepted_variant() -> None:
    scene = default_held_out_scenes()[0]
    snapshot = scene.current_states[0]
    extraction = ReflectionExtraction(
        (),
        (
            CurrentStateProposal(
                "wellbeing",
                scene.user_text,
                0.9,
                (scene.user_text,),
                snapshot.memory_id,
            ),
        ),
    )
    result = EvaluatedStateExtraction(scene, extraction, None, 0.2)

    checks = evaluate((result,))
    summary = summarize((result,), checks)

    assert all(check.passed for check in checks)
    assert summary.precision == 1.0
    assert summary.topic_precision == 1.0


def test_raw_model_trace_is_hidden_unless_explicitly_requested() -> None:
    scene = default_held_out_scenes()[0]
    result = EvaluatedStateExtraction(
        scene,
        _expected_extraction(scene),
        None,
        0.2,
        (ModelStageTrace("conversation_reflection", '{"synthetic":true}'),),
    )
    checks = evaluate((result,))
    summary = summarize((result,), checks)

    hidden = render_markdown((result,), checks, summary)
    shown = render_markdown(
        (result,),
        checks,
        summary,
        include_raw_traces=True,
    )

    assert "synthetic" not in hidden
    assert "conversation_reflection" in shown
    assert '{"synthetic":true}' in shown


def test_evaluation_cli_never_defaults_to_conversation_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WINTER_ANALYSIS_LLM_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["current_state_eval.py"])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2


def _expected_extraction(scene: CurrentStateHeldOutScene) -> ReflectionExtraction:
    # The concrete scene type is intentionally used through its public fields so
    # this fixture mirrors what a model adapter returns rather than sharing scorer logic.
    updates = []
    for expected in scene.expected_updates:
        snapshot = next(
            state
            for state in scene.current_states
            if state.topic == expected.topic
        )
        updates.append(
            CurrentStateProposal(
                expected.topic,
                expected.evidence,
                0.95,
                (expected.evidence,),
                snapshot.memory_id,
            )
        )
    return ReflectionExtraction((), tuple(updates))
