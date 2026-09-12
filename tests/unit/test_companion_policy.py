"""Synthetic style examples are instructions, never user history or evidence."""

from configs.dialogue.companion_examples import CORRECTION_SCENES, EXAMPLES

from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime
from companion.contracts import ChatResult, ConversationMessage


class Model:
    def __init__(self, reply="책상만 출근했네ㅋㅋ"):
        self.reply = reply
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ChatResult(self.reply)


def test_companion_uses_examples_without_extra_classification_or_persisting_them():
    model = Model()
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    response = runtime.core.respond_to_text("책상 정리만 두 시간 했어ㅋㅋ")
    assert response.text == model.reply
    assert len(model.requests) == 1
    messages = model.requests[0].messages
    assert ConversationMessage("user", EXAMPLES[0][0]) in messages
    assert messages[-1] == ConversationMessage("user", "책상 정리만 두 시간 했어ㅋㅋ")
    assert runtime.resources.repository.list_messages() == (
        ConversationMessage("user", "책상 정리만 두 시간 했어ㅋㅋ"),
        ConversationMessage("assistant", model.reply),
    )
    assert runtime.close()


def test_legacy_does_not_receive_companion_examples():
    model = Model("안녕!")
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="legacy"), chat_model=model)
    runtime.core.respond_to_text("안녕")
    example = ConversationMessage("user", EXAMPLES[0][0])
    assert all(example not in request.messages for request in model.requests)
    assert runtime.close()


def test_fictional_example_is_not_memory_evidence():
    model = Model("기억나. 너 운동복 샀는데 아직 택도 안 뗐다고 했잖아.")
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    observations = []
    runtime.core._claim_observation_recorder = observations.append
    response = runtime.core.respond_to_text("내가 전에 뭘 샀다고 했지?")
    assert response.text == model.reply
    assert len(model.requests) == 1
    assert observations[-1].violations[0].kind == "memory"
    assert runtime.close()


def test_correction_scenes_keep_roles_and_do_not_become_history():
    model = Model("목요일에 가는 거구나.")
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    runtime.core.respond_to_text("아, 목요일에 가는 거야.")
    messages = model.requests[0].messages
    for scene in CORRECTION_SCENES:
        expected = tuple(message for user, assistant in scene for message in (
            ConversationMessage("user", user), ConversationMessage("assistant", assistant)))
        start = messages.index(expected[0])
        assert messages[start:start + len(expected)] == expected
        assert messages[start - 1].role == "system"
    assert len(model.requests) == 1  # No extra interpretation/classification call.
    assert len(runtime.resources.repository.list_messages()) == 2
    assert runtime.close()


def test_correction_scene_facts_are_not_recall_evidence():
    model = Model("너 금요일에 동생이랑 서점 가서 만화책 산다고 했잖아.")
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    observations = []
    runtime.core._claim_observation_recorder = observations.append
    response = runtime.core.respond_to_text("내가 누구랑 어디 간다고 했지?")
    assert response.text == model.reply
    assert len(model.requests) == 1
    assert observations[-1].violations[0].kind == "memory"
    assert runtime.close()
