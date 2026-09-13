"""P3-1 contracts with synthetic prompts and fake model outputs only."""

from argparse import Namespace
from dataclasses import replace

from configs.dialogue import companion_examples
import pytest

from companion import user_cli
from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime
from companion.cli.__main__ import build_parser
from companion.contracts import ChatResult
from companion.verbal_style import VerbalStylePlanner, load_verbal_style


class CaptureModel:
    def __init__(self, reply="응."):
        self.reply = reply
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ChatResult(self.reply)


@pytest.mark.parametrize("policy,expected", [
    ("companion", "companion_text"),
    ("legacy", "reference-broadcast-2026-08"),
    ("contextual", "reference-broadcast-2026-08"),
    ("interpreted", "reference-broadcast-2026-08"),
])
def test_bootstrap_default_is_selected_by_policy(policy, expected):
    runtime = build_text_runtime(TextRuntimeConfig(backend="fake", dialogue_policy=policy))
    try:
        assert runtime.resources.style_profile.name == expected
    finally:
        runtime.close()


def test_explicit_reference_override_is_preserved():
    runtime = build_text_runtime(TextRuntimeConfig(
        backend="fake", dialogue_policy="companion", verbal_style="reference_broadcast",
    ))
    try:
        assert runtime.resources.style_profile == load_verbal_style("reference_broadcast")
    finally:
        runtime.close()


def test_both_cli_paths_allow_companion_bootstrap_default(monkeypatch, tmp_path):
    assert build_parser().parse_args(["--dialogue-policy", "companion"]).verbal_style is None
    monkeypatch.setattr(user_cli, "DATA", tmp_path)
    for policy, expected in (("companion", None), ("legacy", "reference_conversation")):
        args = user_cli.configure_persistent_args(Namespace(dialogue_policy=policy))
        assert args.verbal_style == expected


@pytest.mark.parametrize("act", ["answer", "explain", "warning", "memory_candidate", "unknown"])
def test_companion_all_acts_have_no_sentence_or_word_caps(act):
    planner = VerbalStylePlanner(load_verbal_style("companion_text"))
    style = planner.plan_turn(act)
    assert style.plan.max_sentences is None
    assert style.plan.max_words_per_sentence is None
    assert style.plan.register == "plain"
    assert "천우" in style.instruction and "반말" in style.instruction
    assert "설명·비교·교정" in style.instruction and "영어" in style.instruction
    assert "None" not in style.instruction
    assert "문장 이내" not in style.instruction and "단어를 넘기지" not in style.instruction


def test_none_sentence_limit_does_not_remove_independent_word_limit():
    profile = replace(load_verbal_style("companion_text"), max_words_per_sentence=12)
    instruction = VerbalStylePlanner(profile).instruction("answer")
    assert "12단어" in instruction and "문장 이내" not in instruction


def test_long_correction_is_preserved_with_640_token_request():
    reply = (
        "원문: 'They was late.'\n수정: 'They were late.'\n"
        "주어가 복수라 were를 써. 과거 시제는 그대로야. "
        "약속에 늦었다는 뜻도 그대로 유지돼."
    )
    model = CaptureModel(reply)
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    try:
        response = runtime.core.respond_to_text("'They was late.' 고치고 이유도 설명해줘.")
        assert response.text == reply
        assert response.verbal_style.max_sentences is None
        assert response.verbal_style.max_words_per_sentence is None
        assert len(model.requests) == 1
        assert model.requests[0].max_tokens == 640
    finally:
        runtime.close()


def test_fixed_examples_budget_proxy_includes_boundaries_and_correction_scenes():
    # Fake/offline size guard, NOT a real tokenizer count or whole-prefix budget.
    # Charge one unit per character and five per message plus a generation prefix.
    # Reviewer must tokenize the actual Identity v3 + full prompt as well.
    runtime = build_text_runtime(TextRuntimeConfig(backend="fake", dialogue_policy="companion"))
    try:
        examples = runtime.core._context_assembler._examples
        proxy_units = sum(len(m.content) + 5 for m in examples) + 3
        assert proxy_units <= 975  # P3-1 baseline proxy; actual token delta is reviewer-owned.
        assert len(companion_examples.EXAMPLES) == 9
        assert companion_examples.MAX_REPLY_TOKENS == 640
    finally:
        runtime.close()


def test_new_scenes_demonstrate_requested_actions_without_replay_questions():
    pairs = companion_examples.EXAMPLES
    questions = pairs[3][1]
    assert questions.count("?") == 2
    assert "노래" in questions and "쉬는 날" in questions
    assert "천우 편" in pairs[4][1]
    correction = pairs[5][1]
    assert "'She go to school.'" in correction and "'She goes to school.'" in correction
    assert "-es" in correction
    assert "얘기한 게" in pairs[6][1] and "바깥에서 한 일은 없어" in pairs[6][1]
    assert "같은 추상 명사" in companion_examples.REAL_DIALOGUE_INSTRUCTION
    assert "질문 요청에는 실제 질문" in companion_examples.REAL_DIALOGUE_INSTRUCTION


def test_recall_and_meta_complaint_examples_are_brief_and_fictional():
    pairs = dict(companion_examples.EXAMPLES)
    assert pairs["나에 대해 알아?"] == "알지. 로봇 연구하잖아. 오늘은 왜 물어봐?"
    complaint = pairs["왜 나열하듯 말해?"]
    assert complaint == "아, 아는 걸 그대로 읊었네. 그냥 얘기하자."
    assert not any(word in complaint for word in ("진심", "이해", "미안", "죄송"))
    assert (
        "'진심', '이해해', '느껴져' 같은 말로 답을 채우지 마. "
        "구체적인 것을 말하거나 물어봐."
    ) in companion_examples.REAL_DIALOGUE_INSTRUCTION
