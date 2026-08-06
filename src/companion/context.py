"""Build bounded recent conversation context for a single chat request."""

from __future__ import annotations

from collections.abc import Sequence

from companion.contracts import ConversationMessage


class ContextBudgetError(ValueError):
    """Raised when a context budget cannot preserve the current user turn."""


class ConversationContextBuilder:
    """Selects recent messages while preserving their original chronological order."""

    def __init__(self, *, max_messages: int = 12, max_characters: int = 4000) -> None:
        if max_messages < 1:
            raise ContextBudgetError("max_messages must be at least 1")
        if max_characters < 1:
            raise ContextBudgetError("max_characters must be at least 1")
        self._max_messages = max_messages
        self._max_characters = max_characters

    def build(self, messages: Sequence[ConversationMessage]) -> tuple[ConversationMessage, ...]:
        """Keep newest messages within the budget, always retaining the current turn."""
        if not messages:
            return ()

        selected: list[ConversationMessage] = []
        character_count = 0
        for message in reversed(messages):
            is_current_turn = not selected
            exceeds_budget = (
                len(selected) >= self._max_messages
                or character_count + len(message.content) > self._max_characters
            )
            if exceeds_budget and not is_current_turn:
                break
            selected.append(message)
            character_count += len(message.content)
        selected.reverse()
        return tuple(selected)
