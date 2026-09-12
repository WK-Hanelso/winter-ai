import sqlite3

import pytest

from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.context import ConversationContextBuilder
from companion.contracts import ChatResult, ConversationMessage
from companion.core import CompanionCore


def test_recent_window_is_ordered_and_bounded_across_restart(tmp_path):
    path = tmp_path / "history.sqlite"
    repository = SqliteConversationRepository(path)
    for index in range(30):
        repository.append(ConversationMessage("user", f"문장 {index}"))
    reopened = SqliteConversationRepository(path)
    assert [m.content for m in reopened.recent_messages(3)] == ["문장 27", "문장 28", "문장 29"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0] == 30
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]


def test_search_returns_an_old_user_turn_and_its_actual_answer(tmp_path):
    repository = SqliteConversationRepository(tmp_path / "history.sqlite")
    repository.append(ConversationMessage("user", "도예 수업에서 파란 컵을 만들었어."))
    repository.append(ConversationMessage("assistant", "파란 컵이구나."))
    for index in range(20):
        repository.append(ConversationMessage("user", f"무관한 최근 문장 {index}"))
    found = repository.search_messages(("도예", "수업"), exclude_latest=12, limit=2)
    assert tuple(m.content for m in found) == (
        "도예 수업에서 파란 컵을 만들었어.",
        "파란 컵이구나.",
    )
    assert repository.search_messages(("%",), exclude_latest=0, limit=2) == ()


def test_core_does_not_load_the_entire_sqlite_history(tmp_path):
    repository = SqliteConversationRepository(tmp_path / "history.sqlite")
    repository.append(ConversationMessage("user", "나는 도예 수업에서 파란 컵을 만들었어."))
    for index in range(40):
        repository.append(ConversationMessage("user", f"다른 이야기 {index}"))

    def forbidden():
        raise AssertionError("unbounded list_messages in foreground")

    repository.list_messages = forbidden
    requests = []

    class Model:
        def generate(self, request):
            requests.append(request)
            return ChatResult("파란 컵을 만들었어.")

    core = CompanionCore(Model(), repository, ConversationContextBuilder(max_messages=4))
    assert core.respond_to_text("도예 수업에서 뭐 만들었지?").text == "파란 컵을 만들었어."
    assert len(requests) == 1
    assert any("파란 컵" in m.content for m in requests[0].messages)
    assert len([m for m in requests[0].messages if m.role == "user"]) <= 4


@pytest.mark.parametrize("message_limit,character_limit", [(2, 4000), (12, 35)])
def test_recall_has_no_gap_when_recent_window_shrinks(tmp_path, message_limit, character_limit):
    repository = SqliteConversationRepository(tmp_path / "history.sqlite")
    repository.append(ConversationMessage("user", "도예 수업에서 파란 컵 만들었어."))
    for index in range(4):
        repository.append(ConversationMessage("user", f"무관한 다른 이야기 {index}"))
    requests = []

    class Model:
        def generate(self, request):
            requests.append(request)
            return ChatResult("파란 컵이야.")

    core = CompanionCore(Model(), repository, ConversationContextBuilder(
        max_messages=message_limit, max_characters=character_limit,
    ))
    core.respond_to_text("도예 수업에서 뭐 만들었지?")
    assert any("파란 컵" in message.content for message in requests[0].messages)


@pytest.mark.parametrize("with_exclusions", [False, True])
def test_context_exclusions_filter_windows_and_search_but_preserve_audit(
    tmp_path, with_exclusions,
):
    path = tmp_path / "history.sqlite"
    repo = SqliteConversationRepository(path)
    messages = (
        ("user", "도예 첫 수업"), ("assistant", "첫 답변"),
        ("user", "도예 제외할 질문"), ("assistant", "제외 질문의 답변"),
        ("user", "도예 두 번째 수업"), ("assistant", "제외할 답변"),
        ("user", "최근 소식"), ("assistant", "최근 답변"),
        ("user", "도예 제외할 최신 질문"),
    )
    ids = [repo.append(ConversationMessage(*m)).id for m in messages]
    if with_exclusions:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE conversation_context_exclusions (message_id INTEGER PRIMARY KEY)"
            )
            connection.executemany("INSERT INTO conversation_context_exclusions VALUES (?)",
                                   [(ids[i],) for i in (1, 2, 5, 8)])
    # A table installed after construction must be visible, including after restart.
    for repository in (repo, SqliteConversationRepository(path)):
        assert repository.list_messages() == tuple(ConversationMessage(*m) for m in messages)
        expected_recent = messages[6:8] if with_exclusions else messages[7:9]
        assert repository.recent_messages(2) == tuple(
            ConversationMessage(*m) for m in expected_recent
        )
        found = repository.search_messages(("도예",), exclude_latest=2, limit=8)
        expected = (messages[0], messages[4]) if with_exclusions else messages[:6]
        assert found == tuple(ConversationMessage(*m) for m in expected)
        all_found = repository.search_messages(("도예",), exclude_latest=0, limit=8)
        expected_all = expected if with_exclusions else (*messages[:6], messages[8])
        assert all_found == tuple(ConversationMessage(*m) for m in expected_all)
