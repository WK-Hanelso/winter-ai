from companion.contracts import ConversationMessage
from companion.conversation_recall import ConversationRecallRetriever, recall_context


def test_retriever_finds_an_older_related_turn_outside_recent_window() -> None:
    messages = [
        ConversationMessage("user", "겨울이 목소리는 질문 억양이 아직 어색해"),
        ConversationMessage("assistant", "질문 끝을 올리는 앞단부터 다시 보자."),
        ConversationMessage("user", "점심은 김치찌개 먹었어"),
        ConversationMessage("assistant", "따뜻했겠다."),
    ]
    for index in range(12):
        messages.append(ConversationMessage("user", f"최근 다른 대화 {index}"))

    recalled = ConversationRecallRetriever(recent_messages=12).retrieve(
        messages, "목소리 질문은 지금 어떻게 됐어?"
    )

    assert [message.content for message in recalled] == [
        "겨울이 목소리는 질문 억양이 아직 어색해",
        "질문 끝을 올리는 앞단부터 다시 보자.",
    ]
    context = recall_context(recalled)
    assert "오래된 실제 대화" in context
    assert "천우> 겨울이 목소리는" in context


def test_retriever_does_not_inject_unrelated_old_conversation() -> None:
    messages = [
        ConversationMessage("user", "점심은 김치찌개 먹었어"),
        ConversationMessage("assistant", "따뜻했겠다."),
    ] + [ConversationMessage("user", f"최근 대화 {index}") for index in range(12)]

    assert (
        ConversationRecallRetriever(recent_messages=12).retrieve(
            messages, "목소리 억양은 어때?"
        )
        == ()
    )
