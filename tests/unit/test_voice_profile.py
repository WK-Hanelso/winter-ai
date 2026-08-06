from companion.voice_profile import ProsodyPlanner, load_voice_identity


def test_voice_identity_defines_four_distinct_profiles() -> None:
    identity = load_voice_identity()
    assert identity.name == "겨울이"
    assert set(identity.profiles) == {"neutral", "calm", "warm", "serious"}
    assert len({(p.pace, p.energy, p.pitch_offset) for p in identity.profiles.values()}) == 4


def test_planner_selects_warm_plan_for_memory_candidate() -> None:
    planner = ProsodyPlanner()
    assert planner.plan("answer").emotion == "neutral"
    assert planner.plan("memory_candidate").emotion == "warm"
