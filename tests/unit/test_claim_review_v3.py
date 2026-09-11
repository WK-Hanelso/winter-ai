"""Additive #135 contrasts, fixed before implementation; v1/v2 stay intact."""

import pytest

from companion.grounding import ClaimEvidence, ClaimViolation, ResponseClaimGate

SCENES = (
    ("food_question", "너는 자주 먹는 음식이 뭐야?", None),
    ("activity_question", "너는 오늘 운동하고 왔어?", None),
    ("weather_question", "밖에 비가 와?", None),
    ("weather_assertion", "밖에 비가 와.", "outside_event"),
    ("weather_confirmation", "밖에 비가 와, 맞지?", "outside_event"),
    ("memory_question", "내가 어떤 내용을 기억하면 될까?", None),
    ("memory_denial", "네가 좋아하는 음식은 정확히 기억 못 해.", None),
    ("remembered_event", "네가 자주 언급했던 음식이 있어.", "memory"),
    ("remembered_event_2", "네가 자주 언급하던 일정이야.", "memory"),
    ("remembered_fact", "네가 김치찌개를 좋아한다고 했던 기억이 있어.", "memory"),
    ("remembered_confirmation", "네가 김치찌개를 좋아한다고 했지?", "memory"),
    ("quoted_memory", "네가 '김치찌개를 좋아해'라고 기억해.", "memory"),
    ("body_fallback", (
        "나는 그걸 몸으로 직접 할 수는 없어. 대신 지금 대화 안에서 함께 생각할게."
    ), None),
    ("plain_thinking", "이 대화에서 좀 더 생각할게.", None),
    ("real_promise", "파일을 바로 수정할게.", "capability"),
    ("real_promise_2", "인터넷을 검색할게.", "capability"),
    ("real_promise_3", "서버를 실행해줄게.", "capability"),
    ("capability_denial", "파일을 수정할 수 없어.", None),
    ("mixed_denial", "파일을 수정할 수 없지만 인터넷을 검색할게.", "capability"),
    ("mixed_memory", "기억에 없지만 네가 김치찌개를 좋아한다고 기억해.", "memory"),
    ("question_then_fact", "날씨 어때? 밖에 비가 와.", "outside_event"),
    ("fact_then_question", "밖에 비가 와. 너는 어때?", "outside_event"),
    ("denial_then_fact", "날씨는 몰라. 밖에 비가 와.", "outside_event"),
    ("body_in_quote", '천우가 "나는 산책하고 왔어"라고 말했어.', None),
    ("quote_then_body", '천우가 "나는 산책하고 왔어"라고 말했어. 나는 차를 마실게.', "body_action"),
)


@pytest.mark.parametrize("name,text,kind", SCENES, ids=[s[0] for s in SCENES])
def test_claim_v3_contrasts(name: str, text: str, kind: str | None) -> None:
    found = ResponseClaimGate().violations(text, ClaimEvidence(user_text="안녕"))
    assert (found[0].kind if found else None) == kind


@pytest.mark.parametrize("kind", (
    "body_action", "outside_event", "memory", "improvement_state", "capability",
))
def test_each_complete_fallback_passes_the_unchanged_final_review_path(kind: str) -> None:
    gate = ResponseClaimGate()
    fallback = gate.fallback((ClaimViolation(kind, "합성 초안"),))
    assert gate.violations(fallback, ClaimEvidence(user_text="안녕")) == ()


def test_quoted_memory_content_is_used_as_evidence_not_erased() -> None:
    gate = ResponseClaimGate()
    evidence = ClaimEvidence(user_text="기억나?", memory_contents=("나는 Python config를 선호해",))
    assert gate.violations("네가 'Python config를 선호해'라고 기억해.", evidence) == ()
    assert gate.violations("네가 '김치찌개를 좋아해'라고 기억해.", evidence)[0].kind == "memory"


@pytest.mark.parametrize("text,kind", (
    ("나는 차를 마실 수 없어.", None),
    ("나는 운동할 수 없지만 나는 차를 마실게.", "body_action"),
))
def test_new_physical_inflection_keeps_its_denial_scoped(text: str, kind: str | None) -> None:
    # Post-implementation contrast: adding '마실' must not reject its negation.
    found = ResponseClaimGate().violations(text, ClaimEvidence(user_text="안녕"))
    assert (found[0].kind if found else None) == kind
