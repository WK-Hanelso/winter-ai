"""Offline coverage for content comparison against recorded answers."""

from __future__ import annotations

import pytest

from companion.content_evaluation import (
    ContentEvaluationError,
    evaluate_content,
    public_content_summary,
    similarity,
    token_overlap,
)


def test_identical_replies_score_at_the_top() -> None:
    assert similarity("밥 먹었어", "밥 먹었어") == pytest.approx(1.0)
    assert token_overlap("밥 먹었어", "밥 먹었어") == pytest.approx(1.0)


def test_token_overlap_ignores_word_order() -> None:
    # A reply that says the same things in another order should not look as
    # distant as one that says nothing similar.
    assert token_overlap("먹었어 밥을", "밥을 먹었어") == pytest.approx(1.0)
    assert similarity("먹었어 밥을", "밥을 먹었어") < 1.0


def test_unrelated_replies_score_low() -> None:
    assert token_overlap("전혀 다른 이야기", "밥 먹었어") == pytest.approx(0.0)


def test_an_exact_reply_sits_at_the_top_of_the_range() -> None:
    reference = ("밥 먹었어", "영화 봤어", "잠 잤어")

    result = evaluate_content(reference, reference)

    assert result.mean_similarity == pytest.approx(1.0)
    assert result.position == pytest.approx(1.0)


def test_chance_agreement_is_measured_from_recorded_answers_only() -> None:
    # Unrelated Korean sentences already share particles and endings, so the
    # floor is not zero and a raw similarity cannot be read on its own.
    reference = ("밥을 먹었어", "영화를 봤어", "잠을 잤어")

    result = evaluate_content(reference, reference)

    assert result.chance_similarity > 0.0


def test_a_reply_matching_nothing_sits_near_the_floor() -> None:
    generated = ("아무 관련 없는 말", "역시 관련 없음", "여전히 무관")
    reference = ("밥 먹었어", "영화 봤어", "잠 잤어")

    result = evaluate_content(generated, reference)

    assert result.position is not None
    assert result.position < 0.5


def test_identical_recorded_answers_leave_no_room_to_measure() -> None:
    # Chance agreement is already 1.0, so nothing can be distinguished.
    generated = ("같은 말", "같은 말")
    reference = ("같은 말", "같은 말")

    result = evaluate_content(generated, reference)

    assert result.position is None
    assert public_content_summary(result)["chance_leaves_room"] is False


def test_mismatched_counts_are_refused() -> None:
    with pytest.raises(ContentEvaluationError, match="counts must match"):
        evaluate_content(("하나",), ("하나", "둘"))


def test_a_single_pair_cannot_build_a_baseline() -> None:
    with pytest.raises(ContentEvaluationError, match="at least two pairs"):
        evaluate_content(("하나",), ("하나",))


def test_summary_carries_no_reply_text() -> None:
    result = evaluate_content(
        ("오늘 밥 먹었어", "영화 봤어"), ("아침에 밥 먹었어", "영화 봤지")
    )

    serialized = str(public_content_summary(result))

    assert "밥 먹었어" not in serialized
    assert "pair_count" in serialized


def test_pairs_split_in_order_so_holdout_is_later_material() -> None:
    from experiments.content_holdout_eval import split_pairs

    pairs = [{"prompt": str(i), "response": str(i)} for i in range(10)]

    train, holdout = split_pairs(pairs, 0.5)

    assert [pair["prompt"] for pair in train] == ["0", "1", "2", "3", "4"]
    assert [pair["prompt"] for pair in holdout] == ["5", "6", "7", "8", "9"]


def test_a_split_that_empties_a_side_is_refused() -> None:
    from experiments.content_holdout_eval import split_pairs

    pairs = [{"prompt": "a", "response": "b"}, {"prompt": "c", "response": "d"}]

    with pytest.raises(ContentEvaluationError, match="between 0 and 1"):
        split_pairs(pairs, 1.0)
    with pytest.raises(ContentEvaluationError, match="empty side"):
        split_pairs(pairs, 0.1)
