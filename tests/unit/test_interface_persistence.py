from pathlib import Path

from companion import user_cli, voice_cli, web


def test_all_user_interfaces_default_to_the_same_persistent_state() -> None:
    web_args = web.build_parser().parse_args([])
    voice_args = voice_cli.build_parser().parse_args([])

    expected_data = Path("data")
    assert user_cli.DATA == expected_data
    assert web_args.conversation_path == expected_data / "conversations.sqlite"
    assert voice_args.conversation_path == expected_data / "conversations.sqlite"
    assert web_args.memory_path == expected_data / "memories.sqlite"
    assert voice_args.memory_path == expected_data / "memories.sqlite"
    assert web_args.belief_path == expected_data / "beliefs.sqlite"
    assert voice_args.belief_path == expected_data / "beliefs.sqlite"
    assert web_args.dialogue_state_path == expected_data / "dialogue_state.sqlite"
    assert voice_args.dialogue_state_path == expected_data / "dialogue_state.sqlite"
    assert (
        web_args.turn_understanding_path
        == expected_data / "turn_understanding.sqlite"
    )
    assert (
        voice_args.turn_understanding_path
        == expected_data / "turn_understanding.sqlite"
    )
    assert web_args.outcome_path == expected_data / "outcomes.sqlite"
    assert voice_args.outcome_path == expected_data / "outcomes.sqlite"


def test_all_live_interfaces_default_to_the_orin_tunnel() -> None:
    expected = "http://127.0.0.1:18080"

    assert user_cli.build_parser().parse_args([]).model_url == expected
    assert web.build_parser().parse_args([]).model_url == expected
    assert voice_cli.build_parser().parse_args([]).model_url == expected
