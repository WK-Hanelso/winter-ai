"""Character similarity against actual recent replies, never synthetic examples."""

from collections.abc import Sequence
from difflib import SequenceMatcher

# 2026-09-14: real turns 500->512 (ratio 1.000) were six replies apart, so three was too narrow.
RECENT_REPLY_WINDOW = 6

REGENERATION_INSTRUCTION = (
    "방금 초안은 네 직전 답과 거의 같아. 같은 말을 되풀이하지 말고, "
    "사용자의 지금 말에 새로 답해. 반복을 지적받았으면 한 문장으로 인정하고 다른 이야기를 해."
)


def is_repeated_reply(text: str, recent_replies: Sequence[str]) -> bool:
    if len(text) < 20:
        return False
    return any(
        SequenceMatcher(None, previous, text).ratio() >= 0.75
        for previous in recent_replies[-RECENT_REPLY_WINDOW:]
    )
