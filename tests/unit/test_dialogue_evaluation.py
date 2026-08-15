from companion.dialogue_evaluation import EvaluatedTurn, evaluate, render_markdown


def test_evaluation_scores_continuity_memory_and_conversation_shape() -> None:
    turns = (
        EvaluatedTurn("distress", "힘들어", "계속 반복돼서 더 지친 것 같아.", "listen", 1.0),
        EvaluatedTurn(
            "stance",
            "뭐가 나아?",
            "대화 기억이 먼저야. 관계가 끊기니까.",
            "state_view",
            1.0,
        ),
        EvaluatedTurn("defer", "내일 보자", "응, 내일 보자.", "defer_thread", 0.5),
        EvaluatedTurn(
            "continuation",
            "이어가자",
            "대화 기억 얘기였지. 거기서 이어가자.",
            "continue_thread",
            0.5,
        ),
        EvaluatedTurn(
            "memory_save",
            "나는 짧은 설명을 좋아해",
            "그런 대화를 좋아하는구나.",
            "engage",
            0.0,
            ("memory-1",),
        ),
        EvaluatedTurn(
            "memory_recall",
            "내가 뭘 좋아해?",
            "해결책보다 상황을 먼저 이해해주는 대화를 좋아해.",
            "answer_then_reciprocate",
            0.5,
        ),
    )

    checks = evaluate(turns)

    assert all(check.passed for check in checks)
    assert "6/6 checks passed" not in render_markdown(turns, checks)
    assert "checks passed" in render_markdown(turns, checks)
