"""Pure composition of already-selected context. Retrieval and policy stay outside."""

from companion.beliefs import belief_context
from companion.contracts import ChatRequest, ConversationMessage
from companion.conversation_recall import recall_context
from companion.day_reflection import day_reflection_context
from companion.dialogue.character_snapshot import CharacterSnapshot
from companion.memory import Memory, current_state_history_context, memory_context
from companion.open_loops import open_loop_context


class ContextAssembler:
    """Preserve the existing ordered prompt contract without owning a model/store."""

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
    ) -> ChatRequest:
        # Identity leads; style and grounded response move sit nearest the turn.
        system_messages: list[ConversationMessage] = []
        if snapshot.identity is not None:
            system_messages.append(
                ConversationMessage("system", snapshot.identity.system_message())
            )
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
        if style_instruction is not None:
            system_messages.append(ConversationMessage("system", style_instruction))
        final_instruction = "\n\n".join(
            part for part in (grounding_instruction, dialogue_instruction) if part
        )
        system_messages.append(ConversationMessage("system", final_instruction))
        return ChatRequest(
            prompt=text,
            messages=tuple(system_messages) + recent_messages,
            max_tokens=max_tokens,
        )

    def repair_request(self, request: ChatRequest, draft: str, instruction: str) -> ChatRequest:
        """Append the original repair blocks; do not re-read state or re-plan style."""
        return ChatRequest(
            prompt=request.prompt,
            messages=request.messages + (
                ConversationMessage("assistant", draft),
                ConversationMessage("system", instruction),
            ),
            max_tokens=request.max_tokens,
        )
