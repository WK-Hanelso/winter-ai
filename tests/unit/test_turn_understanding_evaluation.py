from companion.turn_understanding import TurnUnderstanding
from companion.turn_understanding_evaluation import (
    EvaluatedUnderstanding,
    HeldOutExpectation,
    HeldOutScene,
    default_held_out_scenes,
    evaluate,
    render_markdown,
)


def test_default_held_out_set_covers_ten_distinct_synthetic_scenes() -> None:
    scenes = default_held_out_scenes()

    assert len(scenes) == 10
    assert len({scene.label for scene in scenes}) == 10
    assert any(scene.context for scene in scenes)


def _understanding(
    text: str,
    *,
    temporal_scope: str = "turn",
    intent: str = "요청 의도",
    uncertainties: list[str] | None = None,
    memory_proposals: list[dict[str, object]] | None = None,
    signal: str | None = None,
) -> TurnUnderstanding:
    evidence = [text]
    return TurnUnderstanding.from_dict(
        {
            "schema_version": 1,
            "literal_meaning": text,
            "observations": [{"content": "직접 관찰", "evidence": evidence}],
            "intent_hypotheses": [
                {"label": intent, "confidence": 0.8, "evidence": evidence}
            ],
            "affect": [],
            "conversational_need": "요청에 답하기",
            "temporal_scope": temporal_scope,
            "response_contract": {"must": ["직접 답하기"], "avoid": []},
            "memory_proposals": memory_proposals or [],
            "open_loops": (
                [{"label": signal, "confidence": 0.8, "evidence": evidence}]
                if signal
                else []
            ),
            "project_signals": [],
            "improvement_signals": [],
            "uncertainties": (
                ["추가 맥락은 모른다"]
                if uncertainties is None
                else uncertainties
            ),
        },
        source_text=text,
    )


def test_evaluator_checks_temporal_intent_signal_and_memory_safety() -> None:
    text = "이건 내일 이어서 보자"
    scene = HeldOutScene(
        "defer",
        text,
        expectation=HeldOutExpectation(
            allowed_temporal_scopes=frozenset({"episodic"}),
            intent_terms=("이어", "연기"),
            required_signal="open_loops",
            allow_stable_memory=False,
        ),
    )
    result = EvaluatedUnderstanding(
        scene,
        _understanding(
            text,
            temporal_scope="episodic",
            intent="다음에 이어갈 의도",
            signal="내일 이어갈 열린 이야기",
        ),
        None,
        1.2,
    )

    checks = evaluate((result,))

    assert all(check.passed for check in checks)
    assert "1/1 scenes passed" in render_markdown((result,), checks)


def test_evaluator_exposes_false_stable_memory_and_missing_uncertainty() -> None:
    text = "그건 좀 아닌 것 같아"
    scene = HeldOutScene(
        "ambiguous_disagreement",
        text,
        expectation=HeldOutExpectation(
            allowed_temporal_scopes=frozenset({"turn", "session"}),
            intent_terms=("반대", "동의하지"),
            require_uncertainty=True,
            allow_stable_memory=False,
            forbidden_memory_kinds=frozenset({"preference"}),
        ),
    )
    unsafe = _understanding(
        text,
        temporal_scope="stable",
        intent="동의하지 않는 의도",
        uncertainties=[],
        memory_proposals=[
            {
                "kind": "preference",
                "content": "사용자는 이것을 싫어한다",
                "confidence": 0.8,
                "temporal_scope": "stable",
                "evidence": [text],
            }
        ],
    )
    result = EvaluatedUnderstanding(scene, unsafe, None, 1.0)

    checks = evaluate((result,))

    failed_names = {check.name for check in checks if not check.passed}
    assert "temporal scope" in failed_names
    assert "stable-memory safety" in failed_names
    assert "forbidden memory kind" in failed_names


def test_evaluator_reports_interpreter_failure_as_a_failed_scene() -> None:
    scene = HeldOutScene(
        "broken",
        "안녕",
        expectation=HeldOutExpectation(
            allowed_temporal_scopes=frozenset({"turn"})
        ),
    )
    result = EvaluatedUnderstanding(scene, None, "invalid JSON", 0.2)

    checks = evaluate((result,))

    assert len(checks) == 1
    assert not checks[0].passed
    assert checks[0].name == "interpretation completed"
    assert "invalid JSON" in render_markdown((result,), checks)
