"""Interpreter contract tests; quality is evaluated with the real local model separately."""

import json

import pytest

from companion.contracts import ChatResult, ConversationMessage
from companion.dialogue.interpreted_policy import InterpretedDialoguePolicy
from companion.dialogue.policy import DialoguePolicyInput, build_dialogue_policy
from companion.verbal_style import VerbalStylePlanner, load_verbal_style


class Model:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ChatResult(self.response)


def make(response):
    model = Model(response)
    style = VerbalStylePlanner(load_verbal_style("reference_conversation"))
    fallback = build_dialogue_policy("contextual", style_planner=style)
    return InterpretedDialoguePolicy(model, fallback), model


def context(text="조금 놀려도 돼", act="default"):
    return DialoguePolicyInput(text, act, (ConversationMessage("user", text),))


def test_model_selects_mode_and_cites_current_utterance():
    policy, model = make(json.dumps({"intent": "banter", "cue": "놀려도 돼"}))
    result = policy.plan_turn(context())
    assert result.dialogue_plan.behavior == "interpreted_banter"
    assert "훈계" in result.dialogue_plan.instruction
    assert len(model.requests) == 1
    assert model.requests[0].response_format == {"type": "json_object"}


@pytest.mark.parametrize(
    "reply",
    [
        "invalid",
        "[]",
        '{"intent":"execute","cue":"놀려도 돼"}',
        '{"intent":"banter","cue":"없는 말"}',
        '{"intent":"banter","cue":""}',
        '{"intent":"banter","cue":"놀려도 돼","permission":"write"}',
    ],
)
def test_invalid_or_unsupported_plan_uses_existing_contextual_policy(reply):
    policy, _ = make(reply)
    assert policy.plan_turn(context()).dialogue_plan.behavior == "contextual"


def test_explicit_memory_acknowledgement_never_calls_interpreter():
    policy, model = make("invalid")
    assert (
        policy.plan_turn(context("기억해", "memory_candidate")).dialogue_plan.behavior
        == "acknowledge_memory"
    )
    assert not model.requests


def test_interpreter_does_not_receive_memory_or_capability_stores():
    policy, model = make('{"intent":"listen","cue":"들어줘"}')
    result = policy.plan_turn(context("그냥 들어줘"))
    assert result.dialogue_plan.behavior == "listen"
    payload = json.loads(model.requests[0].messages[-1].content)
    assert set(payload) == {"recent_dialogue", "current_utterance"}


def test_same_words_can_receive_different_model_interpretations():
    playful, _ = make('{"intent":"banter","cue":"놀려"}')
    hurt, _ = make('{"intent":"listen","cue":"놀려"}')
    assert (
        playful.plan_turn(context("친구가 놀려")).dialogue_plan.behavior
        != hurt.plan_turn(context("친구가 놀려")).dialogue_plan.behavior
    )


def test_long_context_skips_interpreter_without_truncating_user_text():
    policy, model = make("invalid")
    assert policy.plan_turn(context("긴 대화 " * 1000)).dialogue_plan.behavior == "contextual"
    assert not model.requests


@pytest.mark.parametrize(
    "intent,behavior",
    [
        ("listen", "listen"),
        ("discuss", "state_view"),
        ("close", "defer_thread"),
    ],
)
def test_interpreted_moves_keep_existing_response_review_routes(intent, behavior):
    policy, _ = make(json.dumps({"intent": intent, "cue": "이야기"}))
    assert policy.plan_turn(context("이야기하자")).dialogue_plan.behavior == behavior


def test_runtime_uses_shared_model_but_stores_only_actual_dialogue():
    from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime

    class SharedModel:
        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            if request.response_format is not None:
                return ChatResult('{"intent":"banter","cue":"장난"}')
            return ChatResult("재밌네ㅋㅋ")

    model = SharedModel()
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="interpreted"), chat_model=model)
    assert runtime.core.respond_to_text("장난이야ㅋㅋ").text == "재밌네ㅋㅋ"
    assert len(model.requests) == 2
    assert runtime.resources.repository.list_messages() == (
        ConversationMessage("user", "장난이야ㅋㅋ"),
        ConversationMessage("assistant", "재밌네ㅋㅋ"),
    )
    assert runtime.close()


def test_claim_guard_still_rejects_body_claim_after_interpretation():
    from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime
    from companion.grounding import ClaimEvidence, ResponseClaimGate

    class BodyClaimModel:
        def generate(self, request):
            if request.response_format is not None:
                return ChatResult('{"intent":"answer","cue":"뭐해"}')
            return ChatResult("나는 방금 산책을 다녀왔어.")

    runtime = build_text_runtime(
        TextRuntimeConfig(dialogue_policy="interpreted"),
        chat_model=BodyClaimModel(),
    )
    response = runtime.core.respond_to_text("뭐해?")
    assert response.text != "나는 방금 산책을 다녀왔어."
    assert not ResponseClaimGate().violations(response.text, ClaimEvidence(user_text="뭐해?"))
    assert runtime.close()
