"""Offline boundary checks, not a score of generated conversation quality."""

from dataclasses import replace
from io import StringIO
import random

from configs.dialogue.evaluation import SCENES
import pytest

from companion.adapters.fake import InMemoryConversationRepository
from companion.application.bootstrap import TextRuntimeConfig, TextRuntimeError, build_text_runtime
from companion.cli.__main__ import build_parser as developer_parser
from companion.contracts import ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.dialogue.policy import (
    POLICY_NAMES,
    DialoguePolicyError,
    DialoguePolicyInput,
    build_dialogue_policy,
)
from companion.dialogue_act import classify
from companion.grounding import ClaimEvidence, ResponseClaimGate
from companion.memory import (
    MEMORY_BACKGROUND_INSTRUCTION,
    ActiveMemoryRetriever,
    Memory,
    SqliteMemoryRepository,
)
from companion.user_cli import build_parser as user_parser
from companion.verbal_style import VerbalStylePlanner, load_verbal_style


def policy(name):
    return build_dialogue_policy(
        name,
        style_planner=VerbalStylePlanner(
            load_verbal_style("reference_conversation"),
            rng=random.Random(131),
        ),
    )


def context(text="답답한데 해결 방법을 설명해줘"):
    return DialoguePolicyInput(
        text,
        classify(text),
        (ConversationMessage("assistant", "어디가 막혔어?"), ConversationMessage("user", text)),
    )


@pytest.mark.parametrize(
    "name,behavior,register",
    [
        ("legacy", "listen", False),
        ("contextual", "contextual", True),
        ("contextual-moves-only", "contextual", False),
        ("stable-register-only", "listen", True),
    ],
)
def test_independent_policy_axes(name, behavior, register):
    result = policy(name).plan_turn(context())
    assert result.dialogue_plan.behavior == behavior
    assert (result.turn_style.plan.register == "contextual") == register
    if behavior == "listen":
        assert "해결책부터 내놓지 마" in result.dialogue_plan.instruction
        assert result.dialogue_plan.sentence_limit == 1
    else:
        assert "둘 다 다루고" in result.dialogue_plan.instruction
        assert result.dialogue_plan.sentence_limit is None
        assert result.turn_style.plan.max_sentences == 4


def test_register_removal_is_stable_without_losing_length_profile():
    chosen = policy("contextual")
    styles = [chosen.plan_turn(context()).turn_style for _ in range(30)]
    assert all(item == styles[0] for item in styles)
    assert styles[0].plan.max_sentences == 4
    assert "현재 명시적 요청과 직접 합의" in styles[0].instruction
    assert "4문장" in styles[0].instruction


def test_moves_only_preserves_original_register_sequence():
    legacy = policy("legacy")
    candidate = policy("contextual-moves-only")
    original = [legacy.plan_turn(context()).turn_style for _ in range(30)]
    altered = [candidate.plan_turn(context()).turn_style for _ in range(30)]
    assert original == altered
    assert len({item.plan.register for item in original}) == 2


def test_context_and_direct_preferences_select_evidence_instructions():
    memory = Memory(
        "m1",
        "preference",
        "설명은 결론부터 해줘",
        3,
        1.0,
        "active",
        "user_explicit",
        "fixed",
        "fixed",
        None,
        None,
        "conversation:1",
        "durable",
    )
    chosen = policy("contextual")
    bare = replace(context(), recent_messages=())
    without = chosen.plan_turn(bare).dialogue_plan.instruction
    enriched = replace(context(), memories=(memory,))
    with_memory = chosen.plan_turn(enriched).dialogue_plan.instruction
    assert "직접 합의 선호를 참고" not in without
    assert "제공되지 않은 과거" in without
    assert "앞선 발화의 지시 대상" in with_memory
    assert "직접 합의 선호를 참고" in with_memory
    assert "기억의 출처·상태·시점" in with_memory
    assert enriched.direct_preferences == (memory,)
    assert not replace(enriched, memories=(replace(memory, status="candidate"),)).direct_preferences
    assert not replace(
        enriched, memories=(replace(memory, source="reflection_reviewed"),)
    ).direct_preferences


class SpyModel:
    def __init__(self, answer="알겠어."):
        self.requests = []
        self.answer = answer

    def generate(self, request):
        self.requests.append(request)
        return ChatResult(self.answer)


@pytest.mark.parametrize("name", [name for name in POLICY_NAMES if name != "interpreted"])
def test_core_passes_recent_text_and_stores_response_without_extra_model_call(name, tmp_path):
    model = SpyModel()
    repository = InMemoryConversationRepository()
    from companion.context import ConversationContextBuilder

    memories = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    memory = memories.add_candidate(kind="semantic", content="천우는 Python을 사용한다")
    memories.transition(memory.id, "approved")
    memories.transition(memory.id, "active")
    core = CompanionCore(
        model, repository, ConversationContextBuilder(), dialogue_policy=policy(name),
        memory_retriever=ActiveMemoryRetriever(memories),
    )
    core.respond_to_text("설정 이야기를 해볼까?")
    core.respond_to_text("응 그 얘기를 계속하자")
    assert len(model.requests) == 2
    messages = model.requests[1].messages
    history = (
        ConversationMessage("user", "설정 이야기를 해볼까?"),
        ConversationMessage("assistant", "알겠어."),
    )
    memory_at = next(i for i, m in enumerate(messages)
                     if m.role == "system" and m.content.startswith(MEMORY_BACKGROUND_INSTRUCTION))
    assert memory.content in messages[memory_at].content
    assert messages[memory_at + 1:memory_at + 3] == history
    tail = messages[memory_at + 3:-1]
    assert tail and all(m.role == "system" for m in tail)
    assert messages[-1] == ConversationMessage("user", "응 그 얘기를 계속하자")
    assert tuple(m for m in messages if m.role != "system") == history + (messages[-1],)
    assert repository.list_messages()[-1] == ConversationMessage("assistant", "알겠어.")


@pytest.mark.parametrize("name", POLICY_NAMES)
def test_memory_acknowledgement_follows_actual_write_not_generation(name, tmp_path):
    model = SpyModel()
    runtime = build_text_runtime(
        TextRuntimeConfig(
            backend="fake",
            dialogue_policy=name,
            memory_db=tmp_path / "memory.sqlite",
            conversation_db=tmp_path / "conversation.sqlite",
        ),
        chat_model=model,
    )
    response = runtime.core.respond_to_text("기억해. Python config를 선호해")
    assert response.text == "기억해둘게."
    assert model.requests == []
    memory = runtime.resources.memory_repository.get(response.memory_candidate_ids[0])
    assert memory.status == "active" and memory.evidence_ref == "conversation:1"
    runtime.close()


def test_candidate_keeps_claim_safety_and_worker_opt_in():
    model = SpyModel("나는 방금 산책을 다녀왔어.")
    observations = []
    core = CompanionCore(
        model, InMemoryConversationRepository(), dialogue_policy=policy("contextual"),
        claim_observation_recorder=observations.append,
    )
    evidence = ClaimEvidence(user_text="안녕")
    assert ResponseClaimGate().violations(model.answer, evidence)
    response = core.respond_to_text("안녕")
    assert response.text == model.answer
    assert observations[-1].violations[0].kind == "body_action"
    assert len(model.requests) == 1
    model.requests.clear()
    response = core.respond_to_text("코덱스로 README를 확인해줘")
    assert response.worker_error
    assert model.requests == []


def test_unknown_policy_fails_and_does_not_call_model():
    with pytest.raises(DialoguePolicyError, match="Unknown dialogue policy"):
        build_dialogue_policy("typo")
    model = SpyModel()
    with pytest.raises(TextRuntimeError, match="Dialogue policy unavailable"):
        build_text_runtime(TextRuntimeConfig(dialogue_policy="typo"), chat_model=model)
    assert model.requests == []


def test_policy_runtime_error_is_not_a_fallback():
    class BrokenPolicy:
        def plan_turn(self, context):
            raise DialoguePolicyError("synthetic policy failure")

    model = SpyModel()
    core = CompanionCore(model, InMemoryConversationRepository(), dialogue_policy=BrokenPolicy())
    with pytest.raises(DialoguePolicyError, match="synthetic policy failure"):
        core.respond_to_text("hello")
    assert model.requests == []


@pytest.mark.parametrize("parser", [developer_parser, user_parser])
def test_cli_policy_selection_is_explicit_and_defaults_to_legacy(parser):
    assert parser().parse_args([]).dialogue_policy == "legacy"
    for name in POLICY_NAMES:
        assert parser().parse_args(["--dialogue-policy", name]).dialogue_policy == name
    with pytest.raises(SystemExit):
        parser().parse_args(["--dialogue-policy", "typo"])


def test_cli_initialization_reports_unknown_policy():
    from argparse import Namespace

    from companion.cli.__main__ import run

    output = StringIO()
    assert run(Namespace(backend="fake", dialogue_policy="typo", prompt="hi"), stdout=output) == 1
    assert "Dialogue policy unavailable" in output.getvalue()


def test_cli_marks_explicit_candidate_as_experimental():
    from argparse import Namespace

    from companion.cli.__main__ import run

    output = StringIO()
    assert (
        run(Namespace(backend="fake", dialogue_policy="contextual", prompt="hi"), stdout=output)
        == 0
    )
    assert output.getvalue().startswith("Experimental dialogue policy: contextual\n")


def test_scene_manifest_has_disjoint_development_and_reserved_sets():
    ids = [item["id"] for item in SCENES]
    assert len(ids) == len(set(ids)) == 10
    assert {item["split"] for item in SCENES} == {"development", "reserved"}
    expected = {
        "mixed_intent",
        "joke_correction",
        "own_view",
        "uncertain_memory",
        "conversation_end",
    }
    for split in ("development", "reserved"):
        items = [item for item in SCENES if item["split"] == split]
        assert {item["family"] for item in items} == expected
        assert all(len(item["turns"]) >= 2 and item["expect"] for item in items)


@pytest.mark.parametrize(
    "scene", [s for s in SCENES if s["split"] == "development"], ids=lambda s: s["id"]
)
def test_development_scenes_reach_candidate_without_rubric_leak(scene):
    from companion.context import ConversationContextBuilder

    model = SpyModel()
    core = CompanionCore(
        model,
        InMemoryConversationRepository(),
        ConversationContextBuilder(),
        dialogue_policy=policy("contextual"),
    )
    for text in scene["turns"]:
        core.respond_to_text(text)
    assert len(model.requests) == len(scene["turns"])
    request = model.requests[-1]
    assert request.prompt == scene["turns"][-1]
    model_input = "\n".join(message.content for message in request.messages)
    assert scene["turns"][0] in model_input
    assert all(rubric not in model_input for rubric in scene["expect"])
