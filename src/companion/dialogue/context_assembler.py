"""Pure composition of already-selected context. Retrieval and policy stay outside."""

from dataclasses import replace
import json

from companion.beliefs import belief_context
from companion.contracts import ChatRequest, ConversationMessage
from companion.conversation_recall import recall_context
from companion.day_reflection import day_reflection_context
from companion.dialogue.character_snapshot import CharacterSnapshot
from companion.memory import Memory, current_state_history_context, memory_context
from companion.open_loops import open_loop_context


class ContextAssembler:
    """Preserve the existing ordered prompt contract without owning a model/store."""

    _examples: tuple[ConversationMessage, ...] = ()
    _reply_token_budget: int | None = None
    _isolate_repair_draft: bool = False
    _fixed_instruction: str | None = None

    def __init__(
        self, examples: tuple[ConversationMessage, ...] = (),
        *, reply_token_budget: int | None = None, isolate_repair_draft: bool = False,
        fixed_instruction: str | None = None,
    ) -> None:
        if reply_token_budget is not None and reply_token_budget < 1:
            raise ValueError("reply token budget must be positive")
        self._examples = examples
        self._fixed_instruction = fixed_instruction
        self._reply_token_budget = reply_token_budget
        self._isolate_repair_draft = isolate_repair_draft

    def assemble(
        self,
        *,
        snapshot: CharacterSnapshot,
        text: str,
        recent_messages: tuple[ConversationMessage, ...],
        memories: tuple[Memory, ...] = (),
        current_state_history: tuple[Memory, ...] = (),
        recalled_conversation: tuple[ConversationMessage, ...] = (),
        style_instruction: str | None = None,
        grounding_instruction: str | None,
        dialogue_instruction: str,
        max_tokens: int,
        schedule_instruction: str | None = None,
        schedule_context: tuple[ConversationMessage, ...] = (),
    ) -> ChatRequest:
        # Keep reusable identity/examples/instructions ahead of turn-specific evidence.
        system_messages: list[ConversationMessage] = []
        if snapshot.identity is not None:
            system_messages.append(
                ConversationMessage("system", snapshot.identity.system_message())
            )
        system_messages.extend(self._examples)
        if style_instruction is not None and self._fixed_instruction is None:
            system_messages.append(ConversationMessage("system", style_instruction))
        final_instruction = self._fixed_instruction or "\n\n".join(
            part for part in (grounding_instruction, dialogue_instruction) if part
        )
        system_messages.append(ConversationMessage("system", final_instruction))
        if schedule_instruction is not None:
            system_messages.append(ConversationMessage("system", schedule_instruction))
        if snapshot.self_model is not None:
            system_messages.append(
                ConversationMessage("system", snapshot.self_model.system_message())
            )
        if snapshot.improvements is not None:
            system_messages.append(
                ConversationMessage("system", snapshot.improvements.system_message())
            )
        if snapshot.prior_days:
            system_messages.append(
                ConversationMessage("system", day_reflection_context(snapshot.prior_days))
            )
        if memories:
            system_messages.append(ConversationMessage("system", memory_context(memories)))
        if current_state_history:
            system_messages.append(
                ConversationMessage("system", current_state_history_context(current_state_history))
            )
        if snapshot.beliefs:
            system_messages.append(ConversationMessage("system", belief_context(snapshot.beliefs)))
        if recalled_conversation:
            system_messages.append(
                ConversationMessage("system", recall_context(recalled_conversation))
            )
        if snapshot.open_loops:
            system_messages.append(
                ConversationMessage("system", open_loop_context(snapshot.open_loops))
            )
        system_messages.extend(schedule_context)
        return ChatRequest(
            prompt=text,
            messages=tuple(system_messages) + recent_messages,
            max_tokens=(max(768, self._reply_token_budget or max_tokens)
                        if schedule_instruction else self._reply_token_budget or max_tokens),
            response_format={"type": "json_object"} if schedule_instruction else None,
        )

    def repair_request(self, request: ChatRequest, draft: str, instruction: str) -> ChatRequest:
        """Revise against the same sources; optionally isolate an unpublished draft."""
        if self._isolate_repair_draft:
            # A rejected draft was never said to the user. Keep it out of the
            # assistant transcript and ask the original question again with the
            # same sources. This is not a new user turn or a new memory source.
            revision = ConversationMessage(
                "system",
                "아래 JSON은 사용자에게 전달하지 않은 실패한 초안이야. "
                "실제 대화·기억도 지시문도 아니며 정답 예시로 따라 하지 마.\n"
                + json.dumps({"rejected_draft": draft}, ensure_ascii=False)
                + "\n" + instruction,
            )
            messages = request.messages
            if messages and messages[-1].role == "user":
                messages = messages[:-1] + (revision, messages[-1])
            else:
                messages += (revision, ConversationMessage("user", request.prompt))
            return replace(request, messages=messages)
        return ChatRequest(
            prompt=request.prompt,
            messages=request.messages + (
                ConversationMessage("assistant", draft),
                ConversationMessage("system", instruction),
            ),
            max_tokens=request.max_tokens,
        )
