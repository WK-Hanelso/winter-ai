import pytest

from companion.contracts import ChatRequest, ChatResult
from companion.style_rendering import (
    StyleRenderer,
    check_preserved,
    rendering_instruction,
    required_details,
)
from companion.verbal_style import load_verbal_style

PROFILE = load_verbal_style("reference_broadcast")


class ScriptedChatModel:
    """Returns each scripted reply in turn and records what it was asked."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.prompts.append(request.prompt)
        self.systems.append(
            next(message.content for message in request.messages if message.role == "system")
        )
        return ChatResult(text=self._replies.pop(0))


def test_numbers_are_required_without_their_units() -> None:
    # "3시" may legitimately become "세 시"; turning it into "4시" is a
    # different appointment. Only the digits are pinned.
    assert required_details("3시에 2층에서 보자") == ("3", "2")


def test_quoted_spans_are_required_verbatim() -> None:
    assert "회의 취소" in required_details('그 사람이 "회의 취소"라고 했어')


def test_details_are_deduplicated_in_order_of_appearance() -> None:
    assert required_details("3시, 5분, 3번") == ("3", "5")


def test_a_lost_number_is_reported() -> None:
    assert check_preserved("3시에 만나자", "이따 만나자") == ("3",)


def test_a_dropped_negation_is_reported() -> None:
    # The worst failure available here: it reverses the answer and reads
    # perfectly fluently.
    assert check_preserved("그거 안 돼", "그거 돼") == ("부정 표현",)


def test_a_greeting_is_not_mistaken_for_a_negation() -> None:
    # 안녕 begins with 안. Treating it as negation would make every greeting
    # look like a dropped negation.
    assert check_preserved("안녕", "안녕하세요") == ()


def test_an_intact_rewrite_reports_nothing_lost() -> None:
    assert check_preserved("3시에 안 된대", "3시엔 안 돼") == ()


def test_the_instruction_caps_words_but_not_sentences() -> None:
    instruction = rendering_instruction(PROFILE, "plain")

    assert f"{PROFILE.max_words_per_sentence}단어" in instruction
    # Capping total sentences would force the rewrite to drop one of two points.
    assert "문장을 나눠서 전부 말해" in instruction
    assert str(PROFILE.max_sentences) + "문장" not in instruction


def test_the_rewriter_is_told_nothing_about_the_conversation() -> None:
    model = ScriptedChatModel("응 그래")

    StyleRenderer(model, PROFILE).render("네, 알겠습니다.", "plain")

    assert model.prompts == ["네, 알겠습니다."]
    assert "겨울이" not in model.systems[0]


def test_a_clean_rewrite_is_returned_on_the_first_attempt() -> None:
    result = StyleRenderer(ScriptedChatModel("3시에 보자"), PROFILE).render("3시에 봬요", "plain")

    assert result.text == "3시에 보자"
    assert result.attempts == 1
    assert result.fell_back is False


def test_a_dropped_detail_is_retried_with_the_detail_named() -> None:
    model = ScriptedChatModel("이따 보자", "3시에 보자")

    result = StyleRenderer(model, PROFILE).render("3시에 봬요", "plain")

    assert result.text == "3시에 보자"
    assert result.attempts == 2
    assert result.fell_back is False
    assert "3" in model.prompts[1]


def test_a_rewrite_that_keeps_losing_detail_falls_back_to_the_content() -> None:
    result = StyleRenderer(ScriptedChatModel("이따 보자", "나중에 보자"), PROFILE).render(
        "3시에 봬요", "plain"
    )

    # A plain correct answer beats a character-accurate wrong one.
    assert result.text == "3시에 봬요"
    assert result.missing == ("3",)
    assert result.fell_back is True


def test_empty_content_is_returned_without_calling_the_model() -> None:
    model = ScriptedChatModel()

    result = StyleRenderer(model, PROFILE).render("   ", "plain")

    assert result.attempts == 0
    assert model.prompts == []


def test_an_unknown_register_is_rejected() -> None:
    with pytest.raises(ValueError):
        StyleRenderer(ScriptedChatModel(), PROFILE).render("안녕", "존댓말")


def test_zero_attempts_is_rejected() -> None:
    with pytest.raises(ValueError):
        StyleRenderer(ScriptedChatModel(), PROFILE, max_attempts=0)
