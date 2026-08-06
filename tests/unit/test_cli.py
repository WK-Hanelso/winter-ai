from argparse import Namespace
from io import StringIO
from pathlib import Path

from companion.cli.__main__ import run


def test_cli_fake_backend_runs_one_turn() -> None:
    output = StringIO()

    exit_code = run(
        Namespace(backend="fake", model_url="http://unused", prompt="안녕"),
        stdout=output,
    )

    assert exit_code == 0
    assert output.getvalue() == "Companion> fake: 안녕\n"


def test_cli_interactive_session_uses_one_core_for_turns() -> None:
    output = StringIO()

    exit_code = run(
        Namespace(backend="fake", model_url="http://unused", prompt=None),
        stdin=StringIO("첫 번째\n두 번째\n/exit\n"),
        stdout=output,
    )

    assert exit_code == 0
    assert "Companion> fake: 첫 번째\n" in output.getvalue()
    assert "Companion> fake: 두 번째\n" in output.getvalue()


def test_cli_persists_and_shows_history_when_database_is_explicit(tmp_path: Path) -> None:
    database_path = tmp_path / "conversation.sqlite"
    first_output = StringIO()
    run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt="기억될 대화",
            conversation_db=database_path,
            show_history=False,
        ),
        stdout=first_output,
    )
    history_output = StringIO()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt=None,
            conversation_db=database_path,
            show_history=True,
        ),
        stdout=history_output,
    )

    assert first_output.getvalue() == "Companion> fake: 기억될 대화\n"
    assert exit_code == 0
    assert history_output.getvalue() == "user> 기억될 대화\nassistant> fake: 기억될 대화\n"


def test_cli_reports_unavailable_conversation_storage(tmp_path: Path) -> None:
    output = StringIO()
    database_path = tmp_path / "not-a-database"
    database_path.mkdir()

    exit_code = run(
        Namespace(
            backend="fake",
            model_url="http://unused",
            prompt="저장 실패",
            conversation_db=database_path,
            show_history=False,
        ),
        stdout=output,
    )

    assert exit_code == 1
    assert output.getvalue().startswith("Conversation storage unavailable:")
