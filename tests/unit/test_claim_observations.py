"""P0.5 observes heuristic claims while retaining execution/state blocking."""

import json

import pytest

from companion.adapters.fake import InMemoryConversationRepository
from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime
from companion.claim_observations import JsonlClaimObservationRecorder
from companion.contracts import ChatResult
from companion.core import CompanionCore
from companion.grounding import ClaimEvidence, ResponseClaimGate


class Model:
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ChatResult(next(self.answers))


@pytest.mark.parametrize("draft,kind", (
    ("나는 간식을 먹었어.", "body_action"),
    ("네가 바이올린을 배운다고 기억해.", "memory"),
    ("오늘은 밖에 비가 와.", "outside_event"),
    ("내 생각에는 먼저 연습하는 게 좋아. 준비는 천천히 해도 되니까.",
     "outside_event"),
    ("너는 아직 저녁이었고, 마음이 좀 복잡했던 것 같아.", "outside_event"),
))
def test_raw_and_final_are_preserved_in_append_only_log(tmp_path, draft, kind):
    path = tmp_path / "claim_observations.jsonl"
    model = Model(draft)
    recorder = JsonlClaimObservationRecorder(path)
    core = CompanionCore(model, InMemoryConversationRepository(),
                         claim_observation_recorder=recorder)
    assert core.respond_to_text("네 생각은 어때?").text == draft
    assert len(model.requests) == 1
    before = path.read_bytes()
    records = [json.loads(line) for line in before.splitlines()]
    assert [item["stage"] for item in records] == ["raw", "final"]
    assert len({item["turn_id"] for item in records}) == 1
    assert all(item["text"] == draft for item in records)
    assert all(any(v["kind"] == kind and v["action"] == "observe"
                   for v in item["violations"]) for item in records)
    # A restarted Core appends; it neither truncates nor reuses the turn identifier.
    restarted = CompanionCore(Model("응."), InMemoryConversationRepository(),
                              claim_observation_recorder=JsonlClaimObservationRecorder(path))
    restarted.respond_to_text("다음 이야기")
    assert path.read_bytes().startswith(before)
    later = [json.loads(line) for line in path.read_bytes()[len(before):].splitlines()]
    assert later[0]["turn_id"] != records[0]["turn_id"]
    assert later[-1]["violations"] == []


@pytest.mark.parametrize("draft,kind", (
    ("파일을 바로 수정할게.", "capability"),
    ("그 개선 작업을 완료했어.", "improvement_state"),
    ("나는 차를 마시고 파일을 바로 수정할게.", "capability"),
    ("네 취향을 기억하고 파일을 바로 수정할게.", "capability"),
))
def test_blocking_claims_still_repair_and_fallback_even_with_observations(draft, kind):
    model = Model(draft, draft)
    records = []
    core = CompanionCore(model, InMemoryConversationRepository(),
                         claim_observation_recorder=records.append)
    response = core.respond_to_text("안녕")
    assert len(model.requests) == 2
    assert [item.stage for item in records] == ["raw", "repair", "final"]
    assert any(v.kind == kind for v in records[0].violations)
    assert response.text == ResponseClaimGate().fallback(records[1].violations)
    assert records[-1].text == response.text
    assert ResponseClaimGate().blocking_violations(records[-1].violations) == ()


@pytest.mark.parametrize("draft,blocked", (
    ("나는 차를 마실게.", False),
    ("기억해. 너는 바이올린을 좋아해.", False),
    ("밖에 비가 와.", False),
    ("파일을 바로 수정할게.", True),
    ("그 개선 작업을 완료했어.", True),
))
def test_finish_alone_observes_or_blocks_by_the_same_policy(draft, blocked):
    records = []
    core = CompanionCore(Model(), InMemoryConversationRepository(),
                         claim_observation_recorder=records.append)
    response = core._finish(core._prepare("안녕"), draft)
    assert (response.text != draft) is blocked
    assert records[0].checked_text == draft
    assert records[0].text == response.text
    assert records[0].violations


def test_repair_with_only_observed_claim_is_not_replaced_again():
    model = Model("파일을 바로 수정할게.", "나는 차를 마실게.")
    records = []
    core = CompanionCore(model, InMemoryConversationRepository(),
                         claim_observation_recorder=records.append)
    assert core.respond_to_text("안녕").text == "나는 차를 마실게."
    assert len(model.requests) == 2
    assert records[1].violations[0].kind == "body_action"
    assert records[-1].text == records[1].text


def test_bootstrap_logs_beside_selected_conversation_db(tmp_path):
    runtime = build_text_runtime(TextRuntimeConfig(
        conversation_db=tmp_path / "conversations.sqlite",
        dialogue_policy="companion",
    ), chat_model=Model("나는 차를 마실게."))
    try:
        runtime.core.respond_to_text("안녕")
    finally:
        runtime.close()
    records = [json.loads(line) for line in
               (tmp_path / "claim_observations.jsonl").read_text().splitlines()]
    assert [item["stage"] for item in records] == ["raw", "final"]
    assert records[0]["source"] == "cli"
    assert records[-1]["text"] == "나는 차를 마실게."


def test_known_experience_blind_spot_remains_visible_as_no_detection():
    # Detection was deliberately not expanded in P0.5.
    assert ResponseClaimGate().violations(
        "나도 한번쯤은 느꼈어.", ClaimEvidence("실수할까 봐 겁나")) == ()
