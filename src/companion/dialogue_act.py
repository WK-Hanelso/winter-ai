"""Decide what kind of turn this is, so the answer can be the right length.

겨울이 is someone to talk with, and a general assistant's paragraph is the wrong
shape for that. But "always short" is wrong too: asked to explain something, a
two-sentence reply is a refusal wearing a style.

So the length follows the request. Ordinary conversation stays short; an
explicit ask for an explanation is allowed room.

Biased toward short on purpose
------------------------------
Only a clear request lengthens the turn. Missing one costs a short answer the
user can follow up on; treating small talk as a lecture request costs the thing
that made the companion feel like a companion, which is the complaint this
exists to answer.

Questions alone are not the signal. "오늘 어땠어?" is a question and wants a
sentence back. What marks an explanation is being asked to *tell*, *explain*, or
account for a reason at some length.
"""

from __future__ import annotations

import re

ANSWER = "answer"
EXPLAIN = "explain"
MEMORY_CANDIDATE = "memory_candidate"

# Asking to be taught or told about something. Matched loosely because these
# appear inside longer sentences ("이거 좀 설명해줄래?", "왜 그런지 알려줘").
_EXPLANATION = re.compile(
    r"설명|알려\s?줘|알려\s?주|가르쳐|정리해|풀어서|자세히|구체적으로"
    r"|어떻게\s?하는|무슨\s?뜻|뭐가\s?다른|차이가|이유가|왜\s?그런"
)
# A short "왜?" wants a short answer. The marker above catches "왜 그런지";
# this keeps a bare one from being read as a request for a lecture.
_TOO_SHORT_TO_BE_A_REQUEST = 6


def classify(text: str, *, has_memory_candidate: bool = False) -> str:
    """Name this turn. Memory requests win, because they are explicit."""
    if has_memory_candidate:
        return MEMORY_CANDIDATE
    stripped = text.strip()
    if len(stripped) >= _TOO_SHORT_TO_BE_A_REQUEST and _EXPLANATION.search(stripped):
        return EXPLAIN
    return ANSWER
