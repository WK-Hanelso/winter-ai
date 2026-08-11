"""Say already-decided content in the companion's voice.

The companion used to answer and style itself in one model call: the style
instruction ("one sentence, eight words") sat in the same prompt as the
question, so a length rule competed with getting the answer right. Length won.
Asked what to do about being late to a moved meeting, it replied "그러면 3시에
2층에서 만나자" — correct facts, but the actual question went unanswered.

So the turn is split. The first call answers with no style constraint at all and
keeps identity, memory and grounding. This module is the second call, and it is
deliberately the stupid one:

- it does not know the conversation, the memory, or who is speaking
- it does not decide anything, look anything up, or add anything
- it rewrites one piece of text and stops

Everything it needs is the text plus the profile. That makes its prompt short
(so the extra call is cheap), makes it a pure input/output function (so style
can be iterated without running the rest of the pipeline), and keeps the
companion's personality from having to also be its competence.

The risk this introduces is that a rewrite quietly drops or flips something.
``check_preserved`` guards the part of that which can be checked mechanically —
numbers and quoted spans must survive verbatim, and a negated statement must
still read as negated. It is a tripwire, not a proof: a rewrite can still shift
meaning in ways no substring check will see. When the tripwire fires the caller
keeps the unstyled content, because a plain correct answer beats a
character-accurate wrong one.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from companion.contracts import ChatRequest, ConversationMessage
from companion.ports import ChatModel
from companion.verbal_style import REGISTER_INSTRUCTIONS, VerbalStyleProfile

# Digits survive rewriting verbatim or they are wrong: "3시" may become "세 시",
# but a rewrite that turns it into "4시" is a different appointment. Units are
# deliberately excluded — "3시" and "3시에" must both count as keeping "3".
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# Quoted spans are someone's exact words. Paraphrasing them is a fabrication of
# a smaller kind, so they are required to survive too.
_QUOTED = re.compile(r"[\"'‘“]([^\"'’”\n]{1,40})[\"'’”]")
# Korean negation. `안`/`못` only count standing alone: they are also the first
# syllable of ordinary words (안녕, 못하다 is negation but 못을 박다 is a nail).
_NEGATION = re.compile(r"(?<![가-힣])(안|못)(?![가-힣])|없[^\s]*|아니[^\s]*|말고|말아")


@dataclass(frozen=True)
class RenderResult:
    """One rewrite attempt, and whether it was trusted.

    ``fell_back`` is the honest signal: True means the user is reading the
    unstyled answer because the rewrite could not be trusted, not because the
    profile chose to sound like that.
    """

    text: str
    missing: tuple[str, ...]
    attempts: int
    fell_back: bool


def required_details(content: str) -> tuple[str, ...]:
    """The substrings a rewrite must keep, in order of appearance.

    Only what can be checked without understanding the sentence. Everything
    else — implication, hedging, who is being asked to do what — is outside
    what this can see, and the docstring above says so on purpose.
    """
    details: list[str] = []
    for match in _NUMBER.finditer(content):
        if match.group(0) not in details:
            details.append(match.group(0))
    for match in _QUOTED.finditer(content):
        quoted = match.group(1).strip()
        if quoted and quoted not in details:
            details.append(quoted)
    return tuple(details)


def check_preserved(content: str, rendered: str) -> tuple[str, ...]:
    """Report what the rewrite lost. Empty means nothing detectable was lost."""
    lost = [detail for detail in required_details(content) if detail not in rendered]
    if _NEGATION.search(content) and not _NEGATION.search(rendered):
        # A dropped negation reverses the answer, which is the worst failure
        # available here and the one a reader is least likely to notice.
        lost.append("부정 표현")
    return tuple(lost)


def rendering_instruction(profile: VerbalStyleProfile, register: str) -> str:
    """Build the rewriter's whole prompt. No identity, memory or history.

    The sentence cap the single-call policy used is deliberately not here.
    Capping total sentences forces a rewrite to choose which point to drop when
    the content has two; a short speaker with two things to say says two short
    sentences rather than losing one.
    """
    parts = [
        "너는 다시 말하는 역할만 한다. 아래 내용을 말투만 바꿔서 다시 말해.",
        "내용을 빼거나 더하지 마. 숫자와 사실은 그대로 둬. 새로 설명하거나 인사말을 붙이지 마.",
        REGISTER_INSTRUCTIONS[register],
    ]
    length = "문장은 짧게 써. 할 말이 여러 개면 문장을 나눠서 전부 말해."
    if profile.max_words_per_sentence:
        length += f" 한 문장은 {profile.max_words_per_sentence}단어를 넘기지 마."
    parts.append(length)
    if profile.discourse_markers:
        parts.append(
            f"화제를 바꿀 때는 {', '.join(profile.discourse_markers)} 같은 말을 자연스럽게 써."
        )
    if profile.hesitation_usage == "sparing" and profile.hesitation_markers:
        parts.append(
            f"{', '.join(profile.hesitation_markers)} 같은 말은 아주 드물게만 써. "
            "대부분의 문장에는 넣지 마."
        )
    parts.append("바꾼 문장만 출력해. 설명이나 따옴표는 붙이지 마.")
    return " ".join(parts)


class StyleRenderer:
    """Rewrites decided content into one profile's voice."""

    def __init__(
        self,
        chat_model: ChatModel,
        profile: VerbalStyleProfile,
        *,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._chat_model = chat_model
        self._profile = profile
        self._max_attempts = max_attempts

    def render(self, content: str, register: str) -> RenderResult:
        """Rewrite ``content``, retrying once if a detail went missing.

        A retry rather than a repair: telling the model what it dropped is
        cheaper and safer than editing its output, which would mean writing
        Korean by string surgery.
        """
        if register not in REGISTER_INSTRUCTIONS:
            raise ValueError(f"unsupported register: {register}")
        stripped = content.strip()
        if not stripped:
            return RenderResult(text=content, missing=(), attempts=0, fell_back=False)
        instruction = rendering_instruction(self._profile, register)
        missing: tuple[str, ...] = ()
        for attempt in range(1, self._max_attempts + 1):
            prompt = stripped
            if missing:
                prompt = f"{stripped}\n\n반드시 그대로 남겨야 하는 것: {', '.join(missing)}"
            result = self._chat_model.generate(
                ChatRequest(
                    prompt=prompt,
                    messages=(
                        ConversationMessage("system", instruction),
                        ConversationMessage("user", prompt),
                    ),
                )
            )
            rendered = result.text.strip()
            missing = check_preserved(stripped, rendered)
            if not missing:
                return RenderResult(
                    text=rendered, missing=(), attempts=attempt, fell_back=False
                )
        return RenderResult(
            text=stripped, missing=missing, attempts=self._max_attempts, fell_back=True
        )
