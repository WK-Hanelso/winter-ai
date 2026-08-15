from companion.dialogue_director import DialogueDirector
from companion.open_loops import OpenLoop


def loop(content: str = "기억 구조는 내일 계속 만들자") -> OpenLoop:
    return OpenLoop(
        id="loop-1",
        content=content,
        status="open",
        source="user_explicit",
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
        last_recalled_at=None,
    )


def test_distress_turn_listens_before_trying_to_fix() -> None:
    plan = DialogueDirector().plan("같은 실패를 반복해서 너무 답답해", "answer", ())

    assert plan.behavior == "listen"
    assert "해결책" in plan.instruction
    assert "어떤 문제" in plan.instruction
    assert plan.sentence_limit == 1


def test_opinion_request_requires_a_position_not_agreement() -> None:
    plan = DialogueDirector().plan("너는 어떤 게 더 낫다고 생각해?", "answer", ())

    assert plan.behavior == "state_view"
    assert "판단" in plan.instruction
    assert "맞춰" in plan.instruction

    comparison = DialogueDirector().plan(
        "목소리와 대화 기억 중 지금 뭐가 더 중요하다고 생각해?", "answer", ()
    )
    assert comparison.behavior == "state_view"


def test_ambiguous_continuation_uses_retrieved_open_loop() -> None:
    plan = DialogueDirector().plan("아까 얘기 이어가자", "answer", (loop(),))

    assert plan.behavior == "continue_thread"
    assert plan.open_loop_ids == ("loop-1",)
    assert plan.focus == "기억 구조는 내일 계속 만들자"
    assert "미완성 이야기" in plan.instruction


def test_deferring_a_thread_does_not_reopen_it_in_the_same_turn() -> None:
    plan = DialogueDirector().plan(
        "대화 기억은 내일 다시 이어서 보자", "answer", (loop(),)
    )

    assert plan.behavior == "defer_thread"
    assert "지금 더 물어보거나" in plan.instruction
    assert plan.sentence_limit == 1


def test_recall_question_uses_active_memory_without_uncertainty() -> None:
    plan = DialogueDirector().plan(
        "내가 어떤 대화를 좋아한다고 했지?", "answer", (), has_memory=True
    )

    assert plan.behavior == "recall_memory"
    assert "불확실한 표현" in plan.instruction
