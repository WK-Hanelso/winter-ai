import pytest

from companion.dialogue_act import ANSWER, EXPLAIN, MEMORY_CANDIDATE, classify


@pytest.mark.parametrize(
    "text",
    [
        "이거 좀 설명해줄래?",
        "왜 그런지 알려줘",
        "이 둘이 뭐가 다른지 자세히 말해봐",
        "학습이 어떻게 하는 건지 가르쳐줘",
    ],
)
def test_an_explicit_request_for_an_explanation_earns_room(text: str) -> None:
    assert classify(text) == EXPLAIN


@pytest.mark.parametrize(
    "text",
    [
        "오늘 날씨는 어때?",
        "오늘 어땠어?",
        # A bare "왜?" is a question, not a request for a lecture. Questions on
        # their own must not lengthen the turn or every turn becomes long.
        "왜?",
        "발표 망쳤어",
    ],
)
def test_ordinary_conversation_stays_short(text: str) -> None:
    assert classify(text) == ANSWER


def test_a_memory_request_wins_over_an_explanation_marker() -> None:
    # Being asked to remember something is explicit, and its own short reply
    # already exists. It must not be turned into an explanation by a stray word.
    assert (
        classify("이거 기억해줘, 자세히", has_memory_candidate=True) == MEMORY_CANDIDATE
    )
