"""Retrieve exact older conversation turns without promoting them to facts."""

from __future__ import annotations

import re

from companion.contracts import ConversationMessage

_TOKENS = re.compile(r"[0-9A-Za-z가-힣_+-]+")
_KOREAN_SUFFIXES = (
    "으로부터",
    "이라고",
    "에서는",
    "에게서",
    "까지는",
    "부터는",
    "처럼",
    "보다",
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
_STOP = {
    "그거",
    "이거",
    "저거",
    "지금",
    "어떻게",
    "어때",
    "됐어",
    "했어",
    "아직",
    "정말",
    "오늘",
    "최근",
    "다른",
    "대화",
}


class ConversationRecallRetriever:
    """Select a few exact old turns related to the current wording."""

    def __init__(
        self,
        *,
        recent_messages: int = 12,
        max_turns: int = 2,
        max_characters: int = 900,
    ) -> None:
        self._recent_messages = recent_messages
        self._max_turns = max_turns
        self._max_characters = max_characters

    def retrieve(
        self, messages: list[ConversationMessage] | tuple[ConversationMessage, ...], query: str
    ) -> tuple[ConversationMessage, ...]:
        if len(messages) <= self._recent_messages:
            return ()
        query_terms = _terms(query)
        if not query_terms:
            return ()
        older = messages[: -self._recent_messages]
        candidates: list[tuple[int, int, tuple[ConversationMessage, ...]]] = []
        for index, turn in _turns(older):
            turn_terms = _terms(" ".join(message.content for message in turn))
            overlap = len(query_terms & turn_terms)
            if overlap:
                candidates.append((overlap, index, turn))

        selected: list[tuple[int, tuple[ConversationMessage, ...]]] = []
        length = 0
        for _, index, turn in sorted(
            candidates, key=lambda item: (item[0], item[1]), reverse=True
        ):
            turn_length = sum(len(message.content) for message in turn)
            if len(selected) >= self._max_turns or length + turn_length > self._max_characters:
                continue
            selected.append((index, turn))
            length += turn_length

        flattened: list[ConversationMessage] = []
        for _, turn in sorted(selected, key=lambda item: item[0]):
            flattened.extend(turn)
        return tuple(flattened)


def recall_context(messages: tuple[ConversationMessage, ...]) -> str:
    lines = [
        "다음은 현재 말과 관련 있어 검색된 오래된 실제 대화 발췌야.",
        "발췌에 적힌 것만 참고하고, 이후 상황이 같다고 추측하지 마.",
    ]
    labels = {"user": "천우", "assistant": "겨울이"}
    lines.extend(
        f"{labels.get(message.role, message.role)}> {message.content}"
        for message in messages
    )
    return "\n".join(lines)


def _turns(
    messages: list[ConversationMessage] | tuple[ConversationMessage, ...],
) -> tuple[tuple[int, tuple[ConversationMessage, ...]], ...]:
    turns: list[tuple[int, tuple[ConversationMessage, ...]]] = []
    current: list[ConversationMessage] = []
    start = 0
    for index, message in enumerate(messages):
        if message.role == "user" and current:
            turns.append((start, tuple(current)))
            current = []
            start = index
        elif not current:
            start = index
        current.append(message)
    if current:
        turns.append((start, tuple(current)))
    return tuple(turns)


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for match in _TOKENS.finditer(text.lower()):
        token = match.group(0)
        if token in _STOP or len(token) < 2:
            continue
        terms.add(token)
        for suffix in _KOREAN_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                terms.add(token[: -len(suffix)])
                break
    return terms
