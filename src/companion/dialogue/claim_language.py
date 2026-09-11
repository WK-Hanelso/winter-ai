"""Small lexical views for claim review, not a Korean semantic parser.

Keep question/contrast handling out of evidence authorization. Quoted punctuation
must not create false boundaries, and one denial must not license another clause.
"""

import re

QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|\'[^\'\n]*\'|‘[^’\n]*’')
_BOUNDARY = re.compile(r"(?<=[.!?。])\s+|(?<=지만)[,\s]+|\s+(?:하지만|그런데|대신)\s+")
_CONFIRMATION = re.compile(r"맞(?:지|아)|그렇지|했지|했잖|잖아|기억(?:해|나)\s*\?")
_HISTORY = re.compile(
    r"(?:전에|예전에|자주|그때).{0,60}(?:언급|말했|말하)|"
    r"(?:다고|라고)\s*했(?:었)?(?:지|던)|기억(?:이|은|도)?\s*있"
)
_PROMISE = re.compile(r"(?P<action>[가-힣]*?)(?:할\s*수\s*있|해\s*줄게|할게|처리해)")
_CAPABILITY_ACTIONS = frozenset({
    "", "대화", "답변", "기억", "확인", "검색", "읽기", "저장", "삭제", "수정",
    "실행", "설치", "다운로드", "업로드", "처리", "편집", "적용", "사용", "접속",
    "전송", "작성",
})


def claim_units(text: str) -> tuple[str, ...]:
    quoted_spans = tuple(match.span() for match in QUOTED.finditer(text))
    pieces: list[str] = []
    start = 0
    for boundary in _BOUNDARY.finditer(text):
        if any(left <= boundary.start() < right for left, right in quoted_spans):
            continue
        pieces.append(text[start:boundary.start()].strip())
        start = boundary.end()
    pieces.append(text[start:].strip())
    return tuple(piece for piece in pieces if piece)


def has_history_assertion(text: str) -> bool:
    return bool(_HISTORY.search(text))


def is_information_question(text: str) -> bool:
    """Do not exempt confirmation/presupposed history just because it has '?'."""
    return (
        has_question_form(text)
        and not _CONFIRMATION.search(text)
        and not has_history_assertion(text)
    )


def has_question_form(text: str) -> bool:
    """Even a confirmation question cannot authorize its own premise."""
    return bool(re.search(r"\?\s*$|(?:뭐야|어때|할까|일까|나요)\s*$", text))


def has_capability_predicate(text: str) -> bool:
    # A resource mentioned near '생각할게' is not an execution promise.
    # This inventory describes action predicates, never particular test scenes.
    return "가능" in text or any(
        match["action"] in _CAPABILITY_ACTIONS for match in _PROMISE.finditer(text)
    )
