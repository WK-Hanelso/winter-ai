"""Selectable dialogue instructions; no model call, storage write or permission grant."""

from dataclasses import dataclass, replace
from typing import Protocol

from companion.contracts import ConversationMessage
from companion.dialogue_director import DialogueDirector, DialoguePlan
from companion.memory import Memory
from companion.open_loops import OpenLoop
from companion.verbal_style import REGISTER_INSTRUCTIONS, TurnStyle, VerbalStylePlanner

POLICY_NAMES = ("legacy", "contextual", "contextual-moves-only", "stable-register-only")


class DialoguePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class DialoguePolicyInput:
    text: str
    dialogue_act: str
    recent_messages: tuple[ConversationMessage, ...]
    memories: tuple[Memory, ...] = ()
    current_state_history: tuple[Memory, ...] = ()
    open_loops: tuple[OpenLoop, ...] = ()

    @property
    def direct_preferences(self) -> tuple[Memory, ...]:
        return tuple(
            item
            for item in self.memories
            if item.kind == "preference"
            and item.status == "active"
            and item.source in {"user_explicit", "user_direct", "user_edit"}
        )


@dataclass(frozen=True)
class DialoguePolicyTurn:
    dialogue_plan: DialoguePlan
    turn_style: TurnStyle


class DialoguePolicy(Protocol):
    def plan_turn(self, context: DialoguePolicyInput) -> DialoguePolicyTurn: ...


class LegacyDialoguePolicy:
    """The original Director/VerbalStyle combination, including its limitations."""

    def __init__(self, director: DialogueDirector, style_planner: VerbalStylePlanner) -> None:
        self._director = director
        self._style_planner = style_planner

    def plan_turn(self, context: DialoguePolicyInput) -> DialoguePolicyTurn:
        return DialoguePolicyTurn(
            self._director.plan(
                context.text,
                context.dialogue_act,
                context.open_loops,
                has_memory=bool(context.memories or context.current_state_history),
            ),
            self._style_planner.plan_turn(context.dialogue_act),
        )


class ContextualDialoguePolicy:
    """Prompt-based candidate: the same Core LLM interprets the supplied context.

    This is not an emotion detector or learned relationship engine. Independent
    switches support ablations of lexical move overrides and sampled register.
    """

    def __init__(
        self,
        director: DialogueDirector,
        style_planner: VerbalStylePlanner,
        *,
        contextual_moves: bool = True,
        stable_register: bool = True,
    ) -> None:
        self._director = director
        self._contextual_moves = contextual_moves
        self._stable_register = stable_register
        profile = style_planner.profile
        if stable_register:
            # A stable fallback, not a random draw or a persisted user preference.
            register = (
                ("polite_casual" if profile.polite_ratio >= 0.5 else "plain")
                if profile.polite_ratio is not None
                else profile.register
            )
            self._style_planner = VerbalStylePlanner(
                replace(profile, register=register, polite_ratio=None)
            )
        else:
            self._style_planner = style_planner

    def plan_turn(self, context: DialoguePolicyInput) -> DialoguePolicyTurn:
        # This acknowledgement follows an actual explicit memory write in Core,
        # not an inferred feeling or a new lexical override in this candidate.
        if not self._contextual_moves or context.dialogue_act == "memory_candidate":
            plan = self._director.plan(
                context.text,
                context.dialogue_act,
                context.open_loops,
                has_memory=bool(context.memories or context.current_state_history),
            )
        else:
            parts = [
                "현재 발화를 최근 대화와 함께 읽고 천우가 원하는 반응을 직접 골라. "
                "감정 표현과 실용적 부탁이 함께 있으면 둘 다 다루고, "
                "감정 단어 하나로 설명이나 해결을 막지 마. "
                "농담 뒤 정정이나 경계가 나오면 현재 말을 우선하고 분위기 변화에 맞춰. "
                "자기 의견을 물으면 이유 있는 현재 판단을 말하되 경험을 꾸미지 마. "
                "끝내려는 말에는 질문이나 도움 제안으로 대화를 다시 열지 마. "
                "감정·관계는 단정하지 말고 불확실하면 필요한 지점만 자연스럽게 확인해.",
            ]
            if len(context.recent_messages) > 1:
                parts.append("앞선 발화의 지시 대상과 이미 답한 내용을 이어받고 반복해서 묻지 마.")
            if context.direct_preferences:
                parts.append(
                    "제공된 직접 합의 선호를 참고하되 이번 요청과 충돌하면 현재 말을 우선해."
                )
            if context.memories or context.current_state_history:
                parts.append("기억의 출처·상태·시점을 구분하고 기록 밖의 세부사항을 채우지 마.")
            else:
                parts.append(
                    "제공되지 않은 과거를 안다고 하지 말고 지금 대화에서 확인된 범위로 답해."
                )
            if context.open_loops:
                parts.append(
                    "미완성 이야기는 지금 맥락과 관련 있을 때만 이어가고 꺼내기를 강요하지 마."
                )
            plan = DialoguePlan("contextual", " ".join(parts))
        style = self._style_planner.plan_turn(context.dialogue_act)
        if self._stable_register:
            register_instruction = REGISTER_INSTRUCTIONS[style.plan.register]
            instruction = (style.instruction or "").removeprefix(register_instruction).lstrip()
            style = TurnStyle(
                # Model-resolved register is deliberately not reported as an
                # already observed plain/polite delivery.
                replace(style.plan, register="contextual"),
                "말높임은 현재 명시적 요청과 직접 합의를 우선하고 이유 없이 바꾸지 마. "
                "합의가 없다면 다음을 기본으로 해: " + register_instruction + " " + instruction,
            )
        return DialoguePolicyTurn(plan, style)


def build_dialogue_policy(
    name: str,
    *,
    director: DialogueDirector | None = None,
    style_planner: VerbalStylePlanner | None = None,
) -> DialoguePolicy:
    if name not in POLICY_NAMES:
        raise DialoguePolicyError(f"Unknown dialogue policy: {name}")
    director = director if director is not None else DialogueDirector()
    style_planner = style_planner if style_planner is not None else VerbalStylePlanner()
    if name == "legacy":
        return LegacyDialoguePolicy(director, style_planner)
    return ContextualDialoguePolicy(
        director,
        style_planner,
        contextual_moves=name != "stable-register-only",
        stable_register=name != "contextual-moves-only",
    )
