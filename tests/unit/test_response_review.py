from companion.response_review import ResponseReviewer


def test_state_view_rejects_a_non_choice() -> None:
    instruction = ResponseReviewer().repair_instruction(
        "state_view", "상황에 따라 다를 것 같아. 둘 다 중요하니까."
    )

    assert instruction is not None
    assert "두 선택지 중 하나" in instruction


def test_listening_rejects_a_generic_broad_question() -> None:
    instruction = ResponseReviewer().repair_instruction(
        "listen", "정말 답답하겠다. 어떤 부분이 특히 힘든지 말해줄 수 있어?"
    )

    assert instruction is not None
    assert "질문 없이" in instruction


def test_continuation_rejects_a_different_recent_topic() -> None:
    instruction = ResponseReviewer().repair_instruction(
        "continue_thread",
        "겨울이를 만들면서 같은 실패를 반복한다고 했잖아.",
        focus="대화 기억은 내일 다시 이어서 보자",
    )

    assert instruction is not None
    assert "대화 기억은 내일" in instruction
    assert (
        ResponseReviewer().fallback_response(
            "continue_thread", focus="대화 기억은 내일 다시 이어서 보자"
        )
        == "대화 기억 얘기였지. 거기서 이어가자."
    )


def test_specific_natural_response_passes_without_repair() -> None:
    assert (
        ResponseReviewer().repair_instruction(
            "listen", "계속 손봤는데도 나아진다는 확신이 없어서 더 지친 것 같아."
        )
        is None
    )


def test_listen_fallback_keeps_the_declarative_part_and_drops_the_question() -> None:
    assert (
        ResponseReviewer().fallback_response(
            "listen",
            focus=None,
            draft="계속 반복되는 게 답답하구나. 어떤 부분이 힘들어?",
        )
        == "계속 반복되는 게 답답하구나."
    )


def test_active_memory_recall_rejects_unnecessary_uncertainty() -> None:
    instruction = ResponseReviewer().repair_instruction(
        "recall_memory", "그런 대화를 좋아한다고 했던 것 같아."
    )

    assert instruction is not None
    assert "활성 기억" in instruction


def test_active_memory_recall_preserves_both_sides_of_a_comparison() -> None:
    memory = "나는 해결책보다 상황을 먼저 이해해주는 대화를 좋아해"

    instruction = ResponseReviewer().repair_instruction(
        "recall_memory",
        "상황 이해에 중점을 두는 대화를 좋아한다고 했지.",
        memory_focus=memory,
    )

    assert instruction is not None
    assert "해결책보다" in instruction
    assert (
        ResponseReviewer().repair_instruction(
            "recall_memory",
            "해결책을 바로 내놓기보다 상황을 이해해주는 대화를 좋아한다고 했지.",
            memory_focus=memory,
        )
        is None
    )


def test_active_memory_recall_falls_back_to_the_stored_words() -> None:
    memory = "나는 해결책보다 상황을 먼저 이해해주는 대화를 좋아해"

    assert ResponseReviewer().fallback_response(
        "recall_memory",
        focus=None,
        memory_focus=memory,
    ) == f"응, “{memory}”라고 기억해뒀어."
