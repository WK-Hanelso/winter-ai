from argparse import Namespace
from io import StringIO

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.application import bootstrap
from companion.cli.__main__ import run
from companion.contracts import ChatResult, ConversationMessage
from companion.identity import CompanionIdentity, JsonIdentityRepository


def test_draft_uses_identity_name_and_keeps_session_after_model_failure(tmp_path, monkeypatch):
    identity_path = tmp_path / "identity.json"
    JsonIdentityRepository(identity_path).save(CompanionIdentity(
        "겨울이", "대화 프로그램", ("편안함",), ("정직함",),
        ("반말",), ("없는 일을 꾸미지 않음",), "synthetic-session",
    ))

    class Model:
        calls = 0

        def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                raise AdapterUnavailableError("incomplete reply")
            return ChatResult("응, 다시 왔어.")

    monkeypatch.setattr(bootstrap, "build_chat_model", lambda config: Model())
    database = tmp_path / "conversation.sqlite"
    output = StringIO()
    assert run(Namespace(
        backend="local", prompt=None, dialogue_policy="companion", identity_path=identity_path,
        conversation_db=database,
    ), stdin=StringIO("안녕\n다시 말하자\n/exit\n"), stdout=output) == 0
    assert "겨울이 텍스트 초안" in output.getvalue()
    assert "겨울이> 응, 다시 왔어." in output.getvalue()
    assert "incomplete reply" in output.getvalue()
    assert SqliteConversationRepository(database).list_messages() == (
        ConversationMessage("user", "안녕"),
        ConversationMessage("user", "다시 말하자"),
        ConversationMessage("assistant", "응, 다시 왔어."),
    )
