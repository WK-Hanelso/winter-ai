
from companion import user_cli


def test_user_cli_uses_persistent_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    (tmp_path / "identity.json").write_text(
        '{"name":"겨울이","role":"companion","core_personality":[],'
        '"values":[],"relationship_policy":[],"immutable_boundaries":[],'
        '"version":"1"}'
    )
    captured = {}
    monkeypatch.setattr(user_cli, "run", lambda args: captured.update(vars(args)) or 0)
    assert user_cli.main(["--backend", "fake"]) == 0
    assert captured["conversation_db"] == tmp_path / "conversations.sqlite"
    assert captured["memory_db"] == tmp_path / "memories.sqlite"
    assert captured["belief_db"] == tmp_path / "beliefs.sqlite"
    assert captured["dialogue_state_db"] == tmp_path / "dialogue_state.sqlite"
    assert (
        captured["turn_understanding_db"]
        == tmp_path / "turn_understanding.sqlite"
    )
    assert captured["outcome_db"] == tmp_path / "outcomes.sqlite"
    assert captured["reflection_db"] == tmp_path / "reflection.sqlite"
    assert captured["verbal_style"] == "reference_conversation"


def test_user_cli_exposes_current_state_history_command(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    (tmp_path / "identity.json").write_text(
        '{"name":"겨울이","role":"companion","core_personality":[],'
        '"values":[],"relationship_policy":[],"immutable_boundaries":[],'
        '"version":"1"}'
    )
    captured = {}
    monkeypatch.setattr(user_cli, "run", lambda args: captured.update(vars(args)) or 0)

    assert user_cli.main(["--list-current-state-history"]) == 0
    assert captured["list_current_state_history"] is True
