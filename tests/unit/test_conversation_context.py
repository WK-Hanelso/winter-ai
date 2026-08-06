import pytest

from companion.context import ContextBudgetError, ConversationContextBuilder
from companion.contracts import ConversationMessage


def message(role: str, content: str) -> ConversationMessage:
    return ConversationMessage(role=role, content=content)


def test_context_builder_keeps_newest_messages_in_chronological_order() -> None:
    messages = (
        message("user", "one"),
        message("assistant", "two"),
        message("user", "three"),
        message("assistant", "four"),
    )

    context = ConversationContextBuilder(max_messages=3, max_characters=100).build(messages)

    assert context == messages[1:]


def test_context_builder_drops_old_messages_when_character_budget_is_exceeded() -> None:
    messages = (
        message("user", "12345"),
        message("assistant", "67890"),
        message("user", "current-is-always-kept"),
    )

    context = ConversationContextBuilder(max_messages=12, max_characters=10).build(messages)

    assert context == (messages[-1],)


def test_context_builder_rejects_non_positive_budgets() -> None:
    with pytest.raises(ContextBudgetError, match="max_messages"):
        ConversationContextBuilder(max_messages=0)
    with pytest.raises(ContextBudgetError, match="max_characters"):
        ConversationContextBuilder(max_characters=0)
