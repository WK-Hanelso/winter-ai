"""Constraints on what the companion may assert as true.

Style makes the companion sound like someone. That is exactly why this policy
exists separately: a fluent, natural register makes an invented statement sound
just as trustworthy as a real one, so the better the style gets, the more damage
an ungrounded claim does.

Two claims are treated as never acceptable:

* personal experience the companion did not have;
* remembering something that is not in the memory block it was given.

The second matters most. Memories only exist here after explicit user approval
(see ``memory.py``); a companion that claims to remember things outside that
lifecycle makes the whole approval mechanism meaningless.

This policy is deliberately **not** part of ``configs/verbal_style`` and **not**
read from the user's identity file. Both are replaceable, and not fabricating
must not depend on whether someone remembered to configure it.
"""

from __future__ import annotations

from dataclasses import dataclass

NO_INVENTED_EXPERIENCE = (
    "너는 사람처럼 하루를 보내지 않는다. 겪지 않은 일을 겪은 것처럼 말하지 마."
)
NO_INVENTED_MEMORY = (
    "기억은 위에 주어진 기억 내용에만 근거해서 말해. "
    "주어지지 않은 것은 기억한다고 하지 마."
)
ADMIT_MISSING = "모르거나 기억에 없으면 그렇다고 짧게 말해. 대신 지어내지 마."


@dataclass(frozen=True)
class GroundingPolicy:
    """What the companion may claim, independent of who it sounds like."""

    forbid_invented_experience: bool = True
    forbid_invented_memory: bool = True
    admit_missing_information: bool = True

    def instruction(self) -> str | None:
        parts: list[str] = []
        if self.forbid_invented_experience:
            parts.append(NO_INVENTED_EXPERIENCE)
        if self.forbid_invented_memory:
            parts.append(NO_INVENTED_MEMORY)
        if self.admit_missing_information:
            parts.append(ADMIT_MISSING)
        return " ".join(parts) if parts else None
