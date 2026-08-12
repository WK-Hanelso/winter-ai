"""Where one sentence ends and the next begins.

Both sides of the voice path need the same answer. The synthesis server splits
so that a long answer is not one unbroken generation with its pauses in the
wrong places, and the CLI splits so that stage 2 can start converting the first
sentence while stage 1 is still saying the second. Two copies of the rule would
eventually disagree, and the disagreement would be audible.

The model's own text frontend is not used for this. It normalises and splits in
one pass, and its normaliser breaks Korean words apart ("지금은" became "지
금은"), so it is off — which leaves nothing splitting anything.
"""

from __future__ import annotations

import re

# Korean sentence endings, kept with the sentence they end.
SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
# Long enough to hear as a boundary, short enough not to sound like hesitation.
PAUSE_SECONDS = 0.18


def split_sentences(text: str) -> list[str]:
    """One entry per sentence; the whole text when it has no sentence ending."""
    pieces = [piece.strip() for piece in SENTENCE_END.split(text.strip()) if piece.strip()]
    return pieces or [text.strip()]
