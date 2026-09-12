"""Optional local-model turn interpretation, never a permission or memory decision."""

from dataclasses import asdict, replace
import json

from companion.contracts import ChatRequest, ConversationMessage
from companion.dialogue.policy import DialoguePolicy, DialoguePolicyInput, DialoguePolicyTurn
from companion.dialogue_director import DialoguePlan
from companion.ports import ChatModel

_MOVES = {
    "banter": (
        "이번에는 짧은 농담으로 받아쳐. 사용자가 말한 상황의 재미있는 모순을 잡아 "
        "가볍게 놀려도 돼. 훈계나 생산성 조언, 농담하겠다는 선언으로 대신하지 마."
    ),
    "listen": (
        "이번에는 들으며 반응해. 사용자가 실제로 말한 아쉬움이나 서운함을 구체적으로 "
        "받아줘. 해결 방법, 쉬라는 조언, 일반적인 도움 제안을 붙이지 마."
    ),
    "answer": "이번 질문에 바로 답해. 실제 제공된 정보로만 답하고 모르는 부분은 모른다고 해.",
    "discuss": "함께 생각할 네 관점과 짧은 이유를 말해. 사용자에게 무조건 동의할 필요는 없어.",
    "clarify": "뜻이 불분명한 지점 하나만 짧게 확인해. 추측을 사실처럼 말하지 마.",
    "close": "대화를 짧게 마쳐. 새 질문이나 도움 제안으로 붙잡지 마.",
}
_REVIEW_BEHAVIORS = {
    "banter": "interpreted_banter",
    "listen": "listen",
    "answer": "answer_then_reciprocate",
    "discuss": "state_view",
    "clarify": "contextual",
    "close": "defer_thread",
}
_INSTRUCTION = (
    "대답을 쓰지 말고 마지막 사용자 발화가 지금 원하는 대화 반응을 분류해. "
    "사용자의 상태에 대한 진단이나 사실 추출이 아니라 이번 응답 방식만 고르는 작업이야. "
    "최근 대화보다 마지막의 정정·경계를 우선해. "
    "banter=장난·가벼운 놀림을 주고받기, listen=조언 없이 마음을 들어주기, "
    "answer=질문·구체적인 요청에 답하기, discuss=서로의 관점 이야기, "
    "clarify=뜻을 확인해야 함, close=대화 마치기. "
    "예를 들어 '심심한데 웃겨줘'는 banter, '웃을 기분 아니니까 그만'은 listen이야. "
    "JSON 객체 하나만 출력해. 정확히 intent와 cue 두 문자열 필드만 써. "
    "intent는 위 여섯 값 중 하나, cue는 마지막 사용자 발화에서 그대로 복사한 근거 구절이야."
)


class InterpretedDialoguePolicy:
    """An extra bounded call to the SAME model; invalid interpretations fail to the fallback.

    Exact cue validation prevents invented evidence but does not prove semantic accuracy.
    No generated free-form instruction, store write, tool action or permission is accepted.
    """

    def __init__(self, model: ChatModel, fallback: DialoguePolicy) -> None:
        self._model = model
        self._fallback = fallback

    def plan_turn(self, context: DialoguePolicyInput) -> DialoguePolicyTurn:
        if context.dialogue_act == "memory_candidate":
            return self._fallback.plan_turn(context)
        payload = json.dumps(
            {
                "recent_dialogue": [asdict(item) for item in context.recent_messages[-6:]],
                "current_utterance": context.text,
            },
            ensure_ascii=False,
        )
        if len(payload) > 3500:
            # Do not silently truncate a long correction into a different intent.
            return self._fallback.plan_turn(context)
        request = ChatRequest(
            prompt=payload,
            messages=(
                ConversationMessage("system", _INSTRUCTION),
                ConversationMessage("user", payload),
            ),
            max_tokens=96,
            response_format={"type": "json_object"},
            temperature=0.0,
            seed=13740,
        )
        raw = self._model.generate(request).text
        try:
            result = json.loads(raw)
        except (ValueError, TypeError):
            return self._fallback.plan_turn(context)
        if not isinstance(result, dict) or set(result) != {"intent", "cue"}:
            return self._fallback.plan_turn(context)
        intent, cue = result["intent"], result["cue"]
        if (
            not isinstance(intent, str)
            or intent not in _MOVES
            or not isinstance(cue, str)
            or not 2 <= len(cue.strip()) <= 160
            or cue not in context.text
        ):
            return self._fallback.plan_turn(context)
        # A keyword-derived 'explain' must not expand a banter/listen response's style.
        style_context = (
            replace(context, dialogue_act="default")
            if intent in {"banter", "listen", "close"}
            else context
        )
        fallback = self._fallback.plan_turn(style_context)
        plan = DialoguePlan(
            # Reuse existing style review/repair for supported moves; do not bypass
            # those checks by returning a new name for listening or disagreement.
            _REVIEW_BEHAVIORS[intent],
            _MOVES[intent]
            + (
                " 반응은 한국어 반말로 간결하게 해. 실제 대화와 제공된 근거 밖의 "
                "기억·신체 경험·활동은 만들지 마. 해석이 어긋나면 실제 사용자 말을 우선해."
            ),
        )
        return DialoguePolicyTurn(plan, fallback.turn_style)
