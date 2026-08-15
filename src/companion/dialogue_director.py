"""Choose a conversational move without hard-coding Winter's opinions."""

from __future__ import annotations

from dataclasses import dataclass
import re

from companion.open_loops import OpenLoop

_DISTRESS = re.compile(
    r"힘들|답답|속상|우울|불안|걱정|무서|지쳤|짜증|화나|망했|실패|외로|서운"
)
_OPINION = re.compile(
    r"너는.{0,20}(?:어때|생각|판단)|네\s*생각|어떻게\s*생각|"
    r"뭐가\s*(?:더\s*)?(?:낫|좋|중요)|어느\s*(?:쪽|게)|동의해"
)
_CONTINUE = re.compile(r"아까|그\s*(?:얘기|이야기)|이어서|이어\s*가|계속\s*(?:하자|해)")
_GREETING = re.compile(r"^(?:안녕|좋은\s*아침|일어났어|나\s*왔어|돌아왔어)[.!?\s]*$")
_MEMORY_RECALL = re.compile(
    r"기억(?:나|해)|내가\s*(?:전에|예전에|뭐|뭘|어떤)|"
    r"나에\s*대해|내\s*(?:취향|선호)|뭐였|뭐라고\s*했"
)
_DEFER = re.compile(
    r"(?:내일|나중에|다음에|아침에|끝나면).{0,80}"
    r"(?:하자|보자|얘기|이야기|확인|알려|이어|계속)"
)


@dataclass(frozen=True)
class DialoguePlan:
    behavior: str
    instruction: str
    open_loop_ids: tuple[str, ...] = ()
    focus: str | None = None
    sentence_limit: int | None = None


class DialogueDirector:
    """Select response behaviour; content and judgement still belong to the LLM."""

    def plan(
        self,
        text: str,
        dialogue_act: str,
        open_loops: tuple[OpenLoop, ...],
        *,
        has_memory: bool = False,
    ) -> DialoguePlan:
        if dialogue_act == "memory_candidate":
            return DialoguePlan(
                "acknowledge_memory",
                "기억 요청을 이해했다는 반응만 자연스럽게 하고 새 정보를 덧붙이지 마.",
            )
        if open_loops and _DEFER.search(text):
            focus = open_loops[0].content
            return DialoguePlan(
                "defer_thread",
                "천우가 나중에 이어가자고 한 선택을 짧게 받아들여. "
                "지금 더 물어보거나 대화를 다시 열지 말고, "
                "도움을 제안하는 상투적인 말도 붙이지 마.",
                tuple(loop.id for loop in open_loops),
                focus,
                1,
            )
        if open_loops and _CONTINUE.search(text):
            focus = open_loops[0].content
            return DialoguePlan(
                "continue_thread",
                f"이어갈 미완성 이야기는 정확히 이것이야: {focus!r}. "
                "이 이야기에서 지금 이어갈 지점을 짚어. "
                "막연히 '어떤 얘기?'라고 되묻지 말고, 적힌 내용 밖은 꾸미지 마.",
                tuple(loop.id for loop in open_loops),
                focus,
            )
        if _DISTRESS.search(text):
            return DialoguePlan(
                "listen",
                "지금은 해결책부터 내놓지 마. 첫 문장에서 천우가 왜 답답한지 "
                "이미 나온 말에 근거해 한 가지를 구체적으로 짚어. 둘째 문장은 정말 "
                "필요할 때만 구체적인 질문 하나를 해. '어떤 문제야?', '어떤 부분이 힘들어?'처럼 "
                "이미 드러난 내용을 되돌리는 넓은 질문은 하지 마.",
                sentence_limit=1,
            )
        if has_memory and _MEMORY_RECALL.search(text):
            return DialoguePlan(
                "recall_memory",
                "활성 기억에 적힌 내용을 직접 답해. 이미 저장된 내용이므로 '아마', "
                "'것 같아' 같은 불확실한 표현을 쓰지 말고, 기억 밖의 세부사항은 덧붙이지 마.",
            )
        if _OPINION.search(text):
            return DialoguePlan(
                "state_view",
                "천우에게 맞춰 동의하지 말고 현재 네 판단을 첫 문장에서 먼저 말해. "
                "비교를 물었으면 하나를 골라. '둘 다 중요해', '상황에 따라 달라'로 회피하지 마. "
                "둘째 문장에는 짧은 이유나 생각이 바뀔 조건을 말하고 근거 없는 경험은 만들지 마.",
            )
        if dialogue_act == "explain":
            return DialoguePlan(
                "explain",
                "질문에 바로 답한 뒤 핵심 이유를 이어가. 서론이나 도움 제안으로 시작하지 마.",
            )
        if open_loops and _GREETING.search(text.strip()):
            focus = open_loops[0].content
            return DialoguePlan(
                "gentle_callback",
                "먼저 지금 인사에 답해. 그다음 주어진 미완성 이야기는 부담스럽지 않게 "
                "한 번만 물어볼 수 있고, 매번 꺼내야 하는 의무는 없어.",
                tuple(loop.id for loop in open_loops),
                focus,
            )
        if "?" in text:
            return DialoguePlan(
                "answer_then_reciprocate",
                "질문에 먼저 직접 답해. 정말 궁금한 것이 있을 때만 관련 질문 하나를 돌려줘.",
            )
        return DialoguePlan(
            "engage",
            "천우가 말한 내용 중 정서나 의도가 실린 부분에 반응해. "
            "문장을 되풀이하거나 '도와줄까?', '언제든 말해줘' 같은 일반적인 제안으로 끝내지 마.",
        )
