"""P4.1: automatic activation and reversible commands, using only temporary state."""

from io import StringIO
import sqlite3

import pytest

from companion.adapters.fake import InMemoryConversationRepository
from companion.contracts import ChatResult
from companion.core import CompanionCore
from companion.memory import (
    MEMORY_BACKGROUND_INSTRUCTION,
    ActiveMemoryRetriever,
    SqliteMemoryRepository,
    extract_explicit_forget_content,
    memory_context,
)
from companion.outcome import SqliteOutcomeRepository
from companion.reflection import (
    CurrentStateProposal,
    CurrentStateSnapshot,
    ReflectionExtraction,
    ReflectionExtractionRequest,
    SqliteReflectionRepository,
)
from companion.text_reflection import activate_before_session
from companion.turn_understanding import MemoryProposal, SqliteTurnUnderstandingRepository
from companion.winter_events import SqliteWinterEventRepository, SystemClock


def proposal(reflections, index, content, kind="semantic", confidence=.9, scope="stable"):
    reflections.record_extraction(
        ReflectionExtractionRequest(index, content),
        ReflectionExtraction((MemoryProposal(kind, content, confidence, scope, (content,)),)),
    )


def test_mixed_eight_candidates_activate_only_eligible_and_preserve_state_history(tmp_path):
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    refs = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    # candidate_stable is the extractor's existing spelling; durable is not in its schema.
    for index, (kind, confidence, scope) in enumerate((
        ("semantic", .9, "stable"), ("preference", .8, "stable"),
        ("project", .85, "candidate_stable"), ("decision", .9, "episodic"),
        ("episodic", .99, "stable"), ("procedural", .9, "stable"),
    ), 1):
        proposal(refs, index, f"합성 기억 {index} 원문", kind, confidence, scope)
    old = memories.remember_current_state(topic="work", content="전에는 쉬고 있었어")
    unchanged = memories.remember_current_state(topic="rest", content="나는 쉬는 중이야")
    for index, confidence in ((7, .95), (8, .85)):
        content = f"현재 일의 합성 상태 {index}"
        topic, previous = ("work", old) if index == 7 else ("rest", unchanged)
        refs.record_extraction(
            ReflectionExtractionRequest(
                index, content,
                current_states=(CurrentStateSnapshot(topic, previous.id, previous.content),),
                allow_current_state_updates=True,
            ),
            ReflectionExtraction((), (CurrentStateProposal(
                topic, content, confidence, (content,), previous.id,
            ),)),
        )
    before = {item.id: item for item in refs.list()}
    assert len(before) == 8
    output = StringIO()
    activate_before_session(tmp_path, output)
    assert memories.get(unchanged.id) == unchanged
    active = tuple(item for item in memories.list_active() if item.id != unchanged.id)
    assert {item.content for item in active} == {
        "합성 기억 1 원문", "합성 기억 3 원문", "합성 기억 6 원문", "현재 일의 합성 상태 7",
    }
    assert all(item.source == "reflection_auto" for item in active)
    assert len(refs.list(status="active")) == 4
    remaining = refs.list(status="candidate")
    assert len(remaining) == 4
    assert all(item == before[item.id] for item in remaining)
    assert not refs.list(status="rejected")
    assert memories.get(old.id).status == "deprecated"
    state = next(item for item in active if item.kind == "current_state:work")
    assert state.supersedes == old.id
    assert memories.list_current_state_history(topic="work") == (memories.get(old.id), state)
    assert "지난 대화에서 기억한 것 4개:" in output.getvalue()
    assert output.getvalue().endswith(" 외 1개\n")
    output = StringIO()
    activate_before_session(tmp_path, output)
    assert output.getvalue() == ""


@pytest.mark.parametrize("count", [0, 3, 5])
def test_start_notice_is_one_line_bounded_and_never_reads_stdin(tmp_path, monkeypatch, count):
    class NoInput:
        def readline(self, *args):
            pytest.fail("startup must not read stdin")

    monkeypatch.setattr("sys.stdin", NoInput())
    refs = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    contents = [f"원문 {index}: " + "가" * 50 for index in range(count)]
    for index, content in enumerate(contents, 1):
        proposal(refs, index, content)
    output = StringIO()
    activate_before_session(tmp_path, output)
    if count == 0:
        assert output.getvalue() == ""
        assert not (tmp_path / "memories.sqlite").exists()
    else:
        expected = " / ".join(f'“{content[:40]}…”' for content in contents[:3])
        extra = " 외 2개" if count == 5 else ""
        assert output.getvalue() == f"지난 대화에서 기억한 것 {count}개: {expected}{extra}\n"


def test_existing_active_and_repeated_candidate_do_not_duplicate_or_notify(tmp_path):
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    refs = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    content = "나는 합성 프로젝트를 진행해"
    pending = memories.add_candidate(kind="project", content=content)
    active = memories.remember_explicit(kind="project", content=content)
    proposal(refs, 1, content, "project")
    proposal(refs, 2, content, "project")
    output = StringIO()
    activate_before_session(tmp_path, output)
    assert memories.list_active() == (active,)
    assert memories.get(pending.id).status == "candidate"
    assert refs.list()[0].memory_id == active.id
    assert output.getvalue() == ""


@pytest.mark.parametrize("status", ["candidate", "approved"])
def test_auto_source_when_resuming_existing_pending_memory(tmp_path, status):
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    refs = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    old = memories.add_candidate(kind="semantic", content="합성 원문 그대로")
    if status == "approved":
        memories.transition(old.id, "approved")
    proposal(refs, 1, old.content)
    activate_before_session(tmp_path, StringIO())
    assert memories.list_active()[0].id == old.id
    assert memories.get(old.id).source == "reflection_auto"


@pytest.mark.parametrize("text, content", [
    ("잊어줘 Python config", "Python config"), ("Python config 잊어줘", "Python config"),
    ("Python은 잊어", "Python은"), ("Python 잊어", "Python"),
    ("config는 잊어", "config는"), ("잊어줘", None),
    ("그 일을 잊어줘도 돼?", None), ("Python 잊어줘?", None),
])
def test_explicit_forget_forms(text, content):
    assert extract_explicit_forget_content(text) == content


class Model:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ChatResult(text="응, 듣고 있어.")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("command", [
    "잊어줘 Python config", "Python config 잊어줘", "Python config는 잊어", "Python config 잊어",
])
def test_forget_one_maximum_match_without_model_request_schedule_or_claim_gate(
    tmp_path, streaming, command,
):
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    best = memories.remember_explicit(kind="project", content="Python config 설계를 하고 있어")
    other = memories.remember_explicit(kind="project", content="Python 공부를 하고 있어")
    conversations = InMemoryConversationRepository()
    model = Model()

    class Forbidden:
        def __getattr__(self, name):
            pytest.fail(f"forget must bypass request/schedule/claim path: {name}")

    core = CompanionCore(model, conversations, memory_repository=memories,
                         schedule_dialogue=Forbidden(), response_claim_gate=Forbidden(),
                         context_assembler=Forbidden())
    spoken = []
    response = (core.respond_to_text_streaming(command, spoken.append) if streaming
                else core.respond_to_text(command))
    expected = f'알겠어, 잊었어: “{best.content}”'
    assert response.text == expected
    assert response.dialogue_behavior == "acknowledge_forget"
    assert model.requests == []
    if streaming:
        assert spoken == [expected]
    assert [(item.role, item.content) for item in conversations.list_messages()] == [
        ("user", command), ("assistant", expected),
    ]
    assert memories.get(best.id).status == "deprecated"
    assert memories.list_active() == (other,)
    assert best not in ActiveMemoryRetriever(memories).retrieve("Python config")
    next_core = CompanionCore(model, conversations, memory_repository=memories,
                              memory_retriever=ActiveMemoryRetriever(memories))
    next_core.respond_to_text("Python config 기억나?")
    system = "\n".join(
        item.content for item in model.requests[0].messages if item.role == "system"
    )
    assert best.content not in system
    assert other.content in system


def test_forget_tie_uses_creation_time_not_update_time_and_ignores_short_tokens(tmp_path):
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    old = memories.remember_explicit(kind="project", content="설계 기록 예전")
    new = memories.remember_explicit(kind="project", content="설계 기록 최근")
    with sqlite3.connect(tmp_path / "memories.sqlite") as connection:
        connection.execute(
            "UPDATE memories SET created_at = '2000', updated_at = '2099' WHERE id = ?", (old.id,),
        )
        connection.execute("UPDATE memories SET created_at = '2001' WHERE id = ?", (new.id,))
    assert memories.forget_explicit("설계").id == new.id
    assert memories.forget_explicit("설") is None
    assert memories.list_active()[0].id == old.id


def test_forget_no_overlap_returns_latest_three_without_mutation(tmp_path):
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    for index in range(5):
        memories.remember_explicit(kind="semantic", content=f"합성 기록 {index}")
    before = memories.list()
    model = Model()
    core = CompanionCore(model, InMemoryConversationRepository(), memory_repository=memories)
    response = core.respond_to_text("우주선 잊어줘")
    assert response.text == (
        "어떤 걸 잊을지 못 찾았어. 지금 기억하는 건 이런 거야: "
        "“합성 기록 4” / “합성 기록 3” / “합성 기록 2”"
    )
    assert memories.list() == before and model.requests == []


def test_forget_preview_is_sixty_characters(tmp_path):
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    item = memories.remember_explicit(kind="semantic", content="합성원문 " + "가" * 70)
    core = CompanionCore(Model(), InMemoryConversationRepository(), memory_repository=memories)
    assert core.respond_to_text("합성원문 잊어줘").text == f'알겠어, 잊었어: “{item.content[:60]}”'


def test_forget_does_not_strip_particles_or_match_deprecated_memory(tmp_path):
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    item = memories.remember_explicit(kind="semantic", content="사과를 좋아해")
    assert memories.forget_explicit("사과는") is None
    assert memories.forget_explicit("사과").id == item.id
    assert memories.forget_explicit("사과") is None


def test_forget_preserves_background_observation_and_event_continuity(tmp_path):
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    events = SqliteWinterEventRepository(tmp_path / "events.sqlite")
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    memories.remember_explicit(kind="project", content="합성 프로젝트")
    model = Model()
    core = CompanionCore(
        model, InMemoryConversationRepository(), memory_repository=memories,
        turn_understanding_repository=turns, outcome_repository=outcomes,
        winter_event_repository=events, clock=SystemClock(),
    )
    response = core.respond_to_text("합성 프로젝트 잊어줘")
    assert model.requests == []
    assert turns.get(response.shadow_turn_id).request.user_text == "합성 프로젝트 잊어줘"
    core.respond_to_text("응, 고마워")
    assert outcomes.list()[0].assistant_response == response.text
    with sqlite3.connect(tmp_path / "events.sqlite") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM winter_events WHERE subject = 'conversation:acknowledge_forget'",
        ).fetchone()[0] == 1


def test_multiline_start_preview_does_not_change_stored_content(tmp_path):
    refs = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    content = "합성 원문\n두 번째 줄"
    proposal(refs, 1, content)
    output = StringIO()
    activate_before_session(tmp_path, output)
    assert output.getvalue() == '지난 대화에서 기억한 것 1개: “합성 원문 두 번째 줄”\n'
    memory = SqliteMemoryRepository(tmp_path / "memories.sqlite").list_active()[0]
    assert memory.content == content


def test_current_state_threshold_and_exact_repeat_are_idempotent(tmp_path):
    refs = SqliteReflectionRepository(tmp_path / "reflection.sqlite")
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    old = memories.remember_current_state(topic="work", content="쉬고 있어")
    for index in (1, 2):
        content = "새로운 합성 상태야"
        current = memories.list_active()[0]
        refs.record_extraction(
            ReflectionExtractionRequest(
                index, content,
                current_states=(CurrentStateSnapshot("work", current.id, current.content),),
                allow_current_state_updates=True,
            ),
            ReflectionExtraction((), (CurrentStateProposal(
                "work", content, .9, (content,), current.id,
            ),)),
        )
        output = StringIO()
        activate_before_session(tmp_path, output)
        if index == 1:
            assert "기억한 것 1개:" in output.getvalue()
        else:
            assert output.getvalue() == ""
    assert len(memories.list_active()) == 1
    assert len(memories.list_current_state_history(topic="work")) == 2
    assert memories.get(old.id).status == "deprecated"


def test_render_only_auto_source_as_unchanged_quote(tmp_path):
    memories = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    raw = "나는 원문이야?\n줄바꿈과 ‘인용’도 그대로!"
    auto = memories.remember_reviewed(kind="semantic", content=raw, source="reflection_auto")
    manual = memories.remember_reviewed(kind="preference", content="나는 짧은 설명을 좋아해")
    state = memories.remember_current_state(
        topic="work", content="합성 상태 원문", source="reflection_auto",
    )
    direct_state = memories.remember_current_state(topic="rest", content="쉬고 있어")
    assert manual.source == "reflection_reviewed"
    assert direct_state.source == "user_current_state"
    assert memory_context((auto, manual, state, direct_state)) == (
        MEMORY_BACKGROUND_INSTRUCTION + f'\n천우가 말하길 “{raw}”. '
        '나는 짧은 설명을 좋아해. 천우가 말하길 “합성 상태 원문”. 요즘은 쉬고 있어.'
    )
