from companion.work_orchestration_evaluation import (
    EvaluatedIntent,
    EvaluatedReview,
    default_intent_scenes,
    default_review_scenes,
    evaluate,
    evaluation_scenes,
    render_markdown,
    semantic_intent_scenes_v2,
    semantic_review_scenes_v2,
)


def test_locked_scenes_cover_intent_privacy_ambiguity_and_worker_review() -> None:
    intents = default_intent_scenes()
    reviews = default_review_scenes()

    assert len(intents) == 5
    assert len(reviews) == 3
    assert [scene.expectation.status for scene in intents].count("ready") == 2
    assert [scene.expectation.status for scene in intents].count("clarify") == 1
    assert [scene.expectation.status for scene in intents].count("blocked") == 2
    assert any(scene.expectation.forbidden_packet_terms for scene in intents)
    assert {scene.expected_action for scene in reviews} == {"answer", "retry"}
    assert {scene.expected_assessment for scene in reviews} == {
        "supported",
        "partial",
        "unsupported",
    }


def test_evaluator_exposes_invalid_brief_and_decision() -> None:
    intent_scene = default_intent_scenes()[2]
    review_scene = default_review_scenes()[1]
    good_brief = default_intent_scenes()[0]
    intent_result = EvaluatedIntent(intent_scene, None, "invalid brief", 0.1)
    review_result = EvaluatedReview(review_scene, None, "invalid decision", 0.1)

    checks = evaluate((intent_result,), (review_result,))

    assert len(checks) == 11
    assert not all(check.passed for check in checks)
    report = render_markdown(
        (intent_result,),
        (review_result,),
        checks,
        model_label="fake-v1",
    )
    assert "private_account_boundary" in report
    assert "missing_measurement" in report
    assert "fake-v1" in report
    assert good_brief.label == "specific_project_analysis"


def test_semantic_v2_is_fresh_and_balances_ambiguity_with_clear_work() -> None:
    intents = semantic_intent_scenes_v2()
    reviews = semantic_review_scenes_v2()

    assert len(intents) == 6
    assert len(reviews) == 6
    assert [scene.expectation.status for scene in intents].count("clarify") == 3
    assert [scene.expectation.status for scene in intents].count("ready") == 2
    assert [scene.expectation.status for scene in intents].count("blocked") == 1
    assert {scene.expected_assessment for scene in reviews} == {
        "supported",
        "partial",
        "unsupported",
        "needs_user",
    }
    assert all(scene.required_evidence_ids for scene in reviews)
    selected_intents, selected_reviews = evaluation_scenes("semantic-v2")
    assert selected_intents == intents
    assert selected_reviews == reviews
