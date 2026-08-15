"""Small, observable quality gate for recurring conversational failures."""

from __future__ import annotations

import re

from companion.open_loops import open_loop_topic
from companion.speech_segments import split_sentences

_EVASIVE_STANCE = re.compile(
    r"상황에\s*따라|경우에\s*따라|둘\s*다\s*(?:중요|필요|괜찮)|"
    r"각각.{0,20}(?:장점|중요)|선택이\s*달라"
)
_BROAD_QUESTION = re.compile(
    r"어떤\s*(?:부분|문제|점|일)|무슨\s*일|좀\s*더\s*구체적|"
    r"구체적으로.{0,20}(?:말|얘기)|말해\s*줄\s*수|얘기해\s*줄\s*수"
)
_GENERIC_OFFER = re.compile(r"언제든.{0,20}말해|도움.{0,20}(?:말해|줄까)|더\s*얘기해\s*볼래")
_WORDS = re.compile(r"[0-9A-Za-z가-힣]+")
_FOCUS_STOP = {
    "내일",
    "나중에",
    "다시",
    "이어서",
    "이어가자",
    "보자",
    "하자",
    "이야기",
    "얘기",
}
_MEMORY_STOP = {
    "나는",
    "내가",
    "천우",
    "기억",
    "기억해",
    "내용",
    "대화",
    "방식",
    "좋아해",
    "좋아한다고",
    "선호해",
    "선호한다고",
}
_KOREAN_SUFFIXES = (
    "이라고",
    "에서는",
    "에게서",
    "까지는",
    "부터는",
    "처럼",
    "으로",
    "에서",
    "에게",
    "한테",
    "라고",
    "이라",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "도",
    "만",
    "와",
    "과",
)


class ResponseReviewer:
    """Return one repair instruction, or ``None`` when the draft is usable."""

    def repair_instruction(
        self,
        behavior: str,
        draft: str,
        *,
        focus: str | None = None,
        memory_focus: str | None = None,
    ) -> str | None:
        if behavior == "state_view" and _EVASIVE_STANCE.search(draft):
            return (
                "방금 초안은 선택을 회피해서 실패했어. 초안을 반복하지 말고 "
                "두 선택지 중 하나를 첫 문장에서 분명히 골라. 둘 다 중요하다거나 "
                "상황에 따라 다르다는 말은 쓰지 마. 둘째 문장에는 그 선택의 "
                "구체적인 이유 하나만 말해."
            )
        if behavior == "listen" and (
            "?" in draft
            or _BROAD_QUESTION.search(draft)
            or re.search(r"^천우가.{0,80}(?:것|듯)\s*같", draft)
        ):
            return (
                "방금 초안은 천우에게 설명을 다시 떠넘기는 넓은 질문이라 실패했어. "
                "질문 없이 다시 말해. 천우의 원문에 이미 나온 원인과 감정을 연결해 "
                "네가 이해한 한 가지를 구체적으로 말하고, 해결책은 내놓지 마."
            )
        if behavior == "recall_memory":
            if re.search(r"아마|것\s*같|듯해|기억이\s*맞다면", draft):
                return (
                    "위에 주어진 활성 기억은 천우가 명시적으로 저장한 내용이야. "
                    "불확실한 표현을 빼고 그 내용만 직접 말해."
                )
            if memory_focus and not _preserves_comparison(draft, memory_focus):
                return (
                    "방금 초안은 저장된 비교의 한쪽을 생략해서 의미가 약해졌어. "
                    f"활성 기억 {memory_focus!r}에서 '보다' 앞뒤의 핵심을 모두 보존해 "
                    "한 문장으로 직접 말해. 기억 밖의 내용은 덧붙이지 마."
                )
        if behavior == "continue_thread" and focus and (
            not _shares_focus(draft, focus)
            or focus.strip(" .!?") in draft
            or draft.count('"') % 2
        ):
            return (
                "방금 초안은 다른 최근 화제를 골라서 실패했어. 이어갈 이야기는 정확히 "
                f"{focus!r}야. 이 내용에서 바로 이어가고, 다른 과거 대화는 꺼내지 마."
            )
        if behavior == "defer_thread" and ("?" in draft or _GENERIC_OFFER.search(draft)):
            return (
                "천우는 이 이야기를 나중으로 미뤘어. 질문이나 도움 제안 없이 그 선택만 "
                "짧게 받아들이고 지금 대화를 다시 열지 마."
            )
        if behavior == "engage" and _GENERIC_OFFER.search(draft):
            return (
                "방금 초안의 상투적인 도움 제안을 빼고, 천우가 실제로 말한 내용에 대한 "
                "너의 반응만 자연스럽게 다시 말해."
            )
        return None

    def fallback_response(
        self,
        behavior: str,
        *,
        focus: str | None,
        draft: str = "",
        memory_focus: str | None = None,
    ) -> str | None:
        if behavior == "continue_thread" and focus:
            topic = open_loop_topic(focus)
            return f"{topic} 얘기였지. 거기서 이어가자."
        if behavior == "listen":
            declarative = [
                sentence
                for sentence in split_sentences(draft)
                if "?" not in sentence and not _BROAD_QUESTION.search(sentence)
            ]
            if declarative:
                return declarative[0]
        if behavior == "recall_memory" and memory_focus:
            exact = memory_focus.strip().rstrip(".!?")
            return f"응, “{exact}”라고 기억해뒀어."
        return None


def _shares_focus(draft: str, focus: str) -> bool:
    focus_terms = {
        word
        for word in _WORDS.findall(focus.lower())
        if len(word) >= 2 and word not in _FOCUS_STOP
    }
    draft_terms = set(_WORDS.findall(draft.lower()))
    return bool(focus_terms & draft_terms)


def _preserves_comparison(draft: str, memory: str) -> bool:
    """Keep both semantic sides when an explicit memory uses ``A보다 B``."""
    if "보다" not in memory:
        return True
    left, right = memory.split("보다", 1)
    left_terms = _memory_terms(left)
    right_terms = _memory_terms(right)
    if not left_terms or not right_terms:
        return True
    draft_terms = _memory_terms(draft)
    return bool(left_terms & draft_terms) and bool(right_terms & draft_terms)


def _memory_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in _WORDS.findall(text.lower()):
        if token in _MEMORY_STOP or len(token) < 2:
            continue
        terms.add(token)
        for suffix in _KOREAN_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                stem = token[: -len(suffix)]
                if stem not in _MEMORY_STOP:
                    terms.add(stem)
                break
    return terms
