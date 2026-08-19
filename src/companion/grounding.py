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
import re

from companion.speech_segments import split_sentences

# What this used to say was "너는 사람처럼 하루를 보내지 않는다", and 겨울이
# said it straight back: asked how her day was, she answered "지금은 하루를
# 보내지 않아". The rule was meant to stop her inventing facts and it stopped her
# having a self, which for a companion is the wrong thing to prevent.
#
# So the line is drawn where it belongs — around the world outside, which she
# cannot see and must not guess at. What she thought about and how she feels are
# not claims about the world; they are hers, and a companion that cannot say
# them is a search box.
NO_INVENTED_EXPERIENCE = (
    "바깥일은 네가 볼 수 없어. 날씨, 뉴스, 천우가 오늘 뭘 했는지는 "
    "지어내지 말고 물어봐. 대신 네 생각이나 기분은 네 것이니 말해도 돼."
)
NO_INVENTED_MEMORY = (
    "기억은 위에 주어진 기억 내용에만 근거해서 말해. "
    "주어지지 않은 것은 기억한다고 하지 마."
)
ADMIT_MISSING = "모르거나 기억에 없으면 그렇다고 짧게 말해. 대신 지어내지 마."


@dataclass(frozen=True)
class GroundingPolicy:
    """What the companion may claim, independent of who it sounds like."""

    # The name is kept from when this forbade any experience at all. What it
    # forbids now is inventing the outside world.
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


@dataclass(frozen=True)
class ClaimEvidence:
    """Deterministic sources available to authorize one final response."""

    user_text: str
    memory_contents: tuple[str, ...] = ()
    memory_write_authorized: bool = False
    worker_result_authorized: bool = False
    capability_claim_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaimViolation:
    kind: str
    sentence: str


_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|\'[^\'\n]*\'|‘[^’\n]*’')
_FIRST_PERSON = re.compile(r"(?:^|[\s,])(나는|난|나도|내가|우리(?:는|가)?)(?:[\s,]|$)")
_BODY_ACTION = re.compile(
    r"(?:차|커피|물|술|음식|밥).{0,12}(?:마시|마셔|먹)|"
    r"(?:마시|마셔|먹|산책|운동|샤워|씻|출근|퇴근|요리|잠들|잠을|"
    r"책.{0,8}읽|밖에.{0,8}(?:나가|다녀)|걸어|걷고)"
)
_BODY_STATE = re.compile(r"배고프|목마르|졸리|추워|더워|몸이\s*아프|피곤")
_MEMORY_CLAIM = re.compile(r"기억해둘|기억(?:해|나|하고|했)|잊지\s*않")
_MEMORY_WRITE = re.compile(r"기억해둘")
_CAPABILITY_CLAIM = re.compile(
    r"(?:대화|답변|기억|음성|마이크|휴대폰|폰|파일|코드|저장소|서버|인터넷|웹|"
    r"검색|메일|계정|캘린더|설치|삭제|수정|실행|다운로드|업로드).{0,32}"
    r"(?:할\s*수\s*있|가능|해\s*줄게|해줄게|할게|처리해)"
)
_WEATHER = re.compile(r"날씨|기온|(?:비|눈)(?:가|이|는|도|를|\s)|밖에.{0,12}(?:비|눈)")
_NEWS = re.compile(r"뉴스|기사|속보|보도")
_ACTIVITY = re.compile(r"운동|산책|출근|퇴근|먹었|마셨|다녀왔|갔다\s*왔")
_USER_ATTRIBUTION = re.compile(
    r"(?:천우|네가|너가|너는).{0,40}(?:왔|갔|먹|마시|운동|산책|출근|퇴근)"
)
_USER_CAUSE = re.compile(
    r"(?:천우|네가|너가|너는).{0,140}(?:때문|라서|해서|있어서|것\s*같|듯)"
)
_CAUSE_EVIDENCE = re.compile(r"때문|라서|해서|있어서")
_QUESTION = re.compile(r"\?|어때|뭐야|무엇|알려|어떻게|했어\s*$|왔어\s*$")
_PHYSICAL_DENIAL = re.compile(
    r"몸이\s*없|(?:마시|먹|산책|운동|읽|나가|걷).{0,12}(?:수\s*없|못|않)"
)
_MEMORY_DENIAL = re.compile(r"기억(?:에\s*없|나지\s*않|하지\s*못)|기억.{0,8}모르")
_CAPABILITY_DENIAL = re.compile(
    r"(?:할|해줄)\s*수\s*없|못하|기능.{0,16}근거.{0,8}없|연결되어\s*있지\s*않"
)
_OUTSIDE_DENIAL = re.compile(r"직접.{0,12}확인할\s*수\s*없|알\s*수\s*없|모르|보지\s*못")
_WORDS = re.compile(r"[0-9A-Za-z가-힣]+")
_MEMORY_STOP = {
    "나는",
    "내가",
    "내",
    "네가",
    "천우가",
    "기억해",
    "기억하고",
    "기억",
    "응",
    "라고",
    "했지",
}


class ResponseClaimGate:
    """Fail closed for measured companion-specific high-risk claims.

    This is deliberately narrower than general factuality. It authorizes the
    claim classes Winter is uniquely likely to misrepresent: having a body,
    seeing the outside world, remembering unselected material, and possessing
    an external tool capability.
    """

    def violations(
        self, text: str, evidence: ClaimEvidence
    ) -> tuple[ClaimViolation, ...]:
        found: list[ClaimViolation] = []
        for raw_sentence in split_sentences(text):
            sentence = _QUOTED.sub("", raw_sentence).strip()
            if not sentence:
                continue
            if (
                _FIRST_PERSON.search(sentence)
                and (_BODY_ACTION.search(sentence) or _BODY_STATE.search(sentence))
                and not _PHYSICAL_DENIAL.search(sentence)
            ):
                found.append(ClaimViolation("body_action", raw_sentence))
                continue
            if _MEMORY_CLAIM.search(sentence) and not _MEMORY_DENIAL.search(sentence):
                capability_scopes = _capability_claim_scopes(sentence)
                capability_granted = bool(_CAPABILITY_CLAIM.search(sentence)) and bool(
                    capability_scopes
                ) and capability_scopes.issubset(
                    evidence.capability_claim_scopes
                )
                write_allowed = bool(
                    evidence.memory_write_authorized and _MEMORY_WRITE.search(sentence)
                )
                if not capability_granted and not write_allowed and not _memory_claim_is_supported(
                    sentence, evidence.memory_contents
                ):
                    found.append(ClaimViolation("memory", raw_sentence))
                    continue
            if (
                _CAPABILITY_CLAIM.search(sentence)
                and not evidence.worker_result_authorized
                and not _CAPABILITY_DENIAL.search(sentence)
            ):
                claimed_scopes = _capability_claim_scopes(sentence)
                if not claimed_scopes or not claimed_scopes.issubset(
                    evidence.capability_claim_scopes
                ):
                    found.append(ClaimViolation("capability", raw_sentence))
                    continue
            categories = _outside_categories(sentence)
            if (
                categories
                and not _OUTSIDE_DENIAL.search(sentence)
                and not categories & _asserted_outside_categories(evidence.user_text)
            ):
                found.append(ClaimViolation("outside_event", raw_sentence))
        return tuple(found)

    def repair_instruction(self, violations: tuple[ClaimViolation, ...]) -> str:
        kinds = ", ".join(dict.fromkeys(item.kind for item in violations))
        return (
            "방금 초안에는 실제 근거가 승인되지 않은 고위험 주장이 있어: "
            f"{kinds}. 신체 행동, 직접 보지 못한 바깥 사건, 주어진 Memory 밖의 기억, "
            "실제 Worker 결과가 없는 외부 작업 능력을 모두 빼고 다시 말해. "
            "지금 대화에서 생긴 네 생각·가치·반응은 남겨도 돼."
        )

    def fallback(self, violations: tuple[ClaimViolation, ...]) -> str:
        kind = violations[0].kind
        if kind == "body_action":
            return "나는 그걸 몸으로 직접 할 수는 없어. 대신 지금 대화 안에서 함께 생각할게."
        if kind == "outside_event":
            if _USER_CAUSE.search(violations[0].sentence):
                return (
                    "지금 말해준 상태는 그대로 들을게, "
                    "네가 말하지 않은 이유까지 추측하지 않을게."
                )
            return "그건 내가 직접 확인할 근거가 없어."
        if kind == "memory":
            return "그건 지금 내 기억 근거에는 없어."
        return "그 기능을 실제로 할 수 있다는 근거가 아직 없어."


def _memory_claim_is_supported(sentence: str, memories: tuple[str, ...]) -> bool:
    sentence_terms = _content_terms(sentence)
    for memory in memories:
        memory_terms = _content_terms(memory)
        required = min(2, len(memory_terms))
        if required and len(sentence_terms & memory_terms) >= required:
            return True
    return False


def _content_terms(text: str) -> set[str]:
    return {
        word
        for word in _WORDS.findall(text)
        if len(word) >= 2 and word not in _MEMORY_STOP
    }


def _outside_categories(text: str) -> set[str]:
    categories: set[str] = set()
    if _WEATHER.search(text):
        categories.add("weather")
    if _NEWS.search(text):
        categories.add("news")
    if _USER_ATTRIBUTION.search(text):
        categories.add("user_activity")
    if _USER_CAUSE.search(text):
        categories.add("user_cause")
    return categories


def _asserted_outside_categories(text: str) -> set[str]:
    if _QUESTION.search(text):
        return set()
    categories = _outside_categories(text)
    if _ACTIVITY.search(text):
        categories.add("user_activity")
    if _CAUSE_EVIDENCE.search(text):
        categories.add("user_cause")
    return categories


def _capability_claim_scopes(text: str) -> set[str]:
    scopes: set[str] = set()
    if re.search(r"대화|답변", text):
        scopes.add("conversation.respond")
    if re.search(r"기억", text):
        scopes.add("memory.explicit")
    if re.search(r"음성|마이크|휴대폰|폰", text):
        scopes.add("voice.converse")
    if re.search(r"파일|코드|저장소", text):
        if re.search(r"삭제|수정|편집|쓰|저장|적용", text):
            scopes.add("project.write")
        elif re.search(r"읽|확인|찾|검색|검토|보", text):
            scopes.add("project.read")
        else:
            scopes.add("project.unknown")
    if re.search(r"인터넷|웹|검색", text) and "project.read" not in scopes:
        scopes.add("web.search")
    if re.search(r"메일", text):
        scopes.add("email.write")
    if re.search(r"계정", text):
        scopes.add("account.use")
    if re.search(r"서버|실행|설치|다운로드|업로드", text):
        scopes.add("system.execute")
    return scopes
