from argparse import Namespace
from io import StringIO

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
