from io import StringIO

from companion import user_cli


def test_user_cli_uses_persistent_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    (tmp_path / "identity.json").write_text('{"name":"겨울이","role":"companion","core_personality":[],"values":[],"relationship_policy":[],"immutable_boundaries":[],"version":"1"}')
    captured = {}
    monkeypatch.setattr(user_cli, "run", lambda args: captured.update(vars(args)) or 0)
    assert user_cli.main(["--backend", "fake"]) == 0
    assert captured["conversation_db"] == tmp_path / "conversations.sqlite"
    assert captured["memory_db"] == tmp_path / "memories.sqlite"
