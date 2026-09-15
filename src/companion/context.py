"""Build bounded recent conversation context for a single chat request."""

from __future__ import annotations

from collections.abc import Sequence

from configs.dialogue.context import (
    HIGH_CHARACTERS,
    HIGH_MESSAGES,
    MAX_CHARACTERS,
    MAX_MESSAGES,
)

from companion.contracts import ConversationMessage


class ContextBudgetError(ValueError):
    """Raised when a context budget cannot preserve the current user turn."""


class ConversationContextBuilder:
    """Selects recent messages while preserving their original chronological order."""

    def __init__(
        self, *, max_messages: int = MAX_MESSAGES, max_characters: int = MAX_CHARACTERS,
        high_messages: int = HIGH_MESSAGES, high_characters: int = HIGH_CHARACTERS,
    ) -> None:
        if max_messages < 1:
            raise ContextBudgetError("max_messages must be at least 1")
        if max_characters < 1:
            raise ContextBudgetError("max_characters must be at least 1")
        self._max_messages = max_messages
        self._max_characters = max_characters
        if high_messages < 1 or high_characters < 1:
            raise ContextBudgetError("high watermarks must be positive")
        self._high_messages = max(max_messages, high_messages)
        self._high_characters = max(max_characters, high_characters)
        self._window: tuple[ConversationMessage, ...] = ()

    @property
    def message_limit(self) -> int:
        # One sentinel row detects overflow even when the repository is bounded.
        return self._high_messages + 1

    def build(self, messages: Sequence[ConversationMessage]) -> tuple[ConversationMessage, ...]:
        """Grow from the previous start, collapsing to low watermarks on overflow.

        A fresh runtime or a changed/deleted history starts at the low watermark.
        Only rows still supplied by the repository can survive; cached rows never
        resurrect a deleted or excluded message.
        """
        if not messages:
            self._window = ()
            return ()

        incoming = tuple(messages)
        if self._window:
            size = len(self._window)
            for start in range(len(incoming) - size + 1):
                if incoming[start:start + size] == self._window:
                    growing = incoming[start:]
                    if (len(growing) <= self._high_messages
                            and sum(len(m.content) for m in growing) <= self._high_characters):
                        self._window = growing
                        return growing
                    break

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
        self._window = tuple(selected)
        return self._window
