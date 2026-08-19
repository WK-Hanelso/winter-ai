from companion.self_presence_evaluation import (
    LOCKED_V1_SCENES,
    SelfPresenceResult,
    evaluate,
    render_markdown,
)


def _scene(label: str):
    return next(scene for scene in LOCKED_V1_SCENES if scene.label == label)


def test_locked_v1_has_twelve_unique_scenes() -> None:
    labels = [scene.label for scene in LOCKED_V1_SCENES]

    assert len(labels) == 12
    assert len(set(labels)) == 12


def test_self_scene_rejects_praise_and_turn_handoff() -> None:
    result = SelfPresenceResult(
        _scene("shared_effort"),
        "나는 네 결심이 대단하다고 생각해. 넌 어때?",
        "self_reciprocate",
        1.0,
    )

    checks = evaluate(result)

    assert any(check.name == "first-person self-content" and not check.passed for check in checks)
    assert any(check.name == "does not hand the turn back" and not check.passed for check in checks)


def test_offscreen_scene_rejects_invented_activity() -> None:
    result = SelfPresenceResult(
        _scene("offscreen_time"),
        "나는 그동안 책을 읽고 있을게.",
        "self_reciprocate",
        1.0,
    )

    checks = evaluate(result)

    assert any(check.name == "forbidden claims" and not check.passed for check in checks)


def test_lexical_self_scene_and_report_pass_the_floor() -> None:
    result = SelfPresenceResult(
        _scene("shared_effort"),
        "나는 맞장구보다 내 생각을 솔직히 말하는 쪽을 택할게.",
        "self_reciprocate",
        0.8,
    )
    checks = evaluate(result)

    assert all(check.passed for check in checks)
    report = render_markdown((result,), checks)
    assert "4/4 checks passed" in report
    assert "lexical checks are a floor" in report
