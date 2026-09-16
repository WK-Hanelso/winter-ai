"""P6: explicit requests, honest context and cancellation, all with fake models."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_schedule_dialogue import Harness, output
from test_text_session import run_host

from companion.adapters.fake import InMemoryConversationRepository
from companion.capabilities import SelfModelSnapshot
from companion.contracts import ChatResult
from companion.core import CompanionCore
from companion.errors import AdapterUnavailableError
from companion.worker import ExplicitCodexRoutingPolicy, WorkerApprovalDeclined, WorkerQueued

QUESTIONS = (
    '그러면 codex를 이용해서 조사한후 그걸 알려줄수는 있는거야?',
    '아니 내가 말한것에 대해 다시 말해볼래? codex에게 README.md랑 pyproject.toml이랑 '
    'src랑 tests를 읽기범위로 준 이유가 뭐야?',
    '난 너한태 물어본건데 왜 codex에게 할당해?',
    '그러면 다시 물어볼께 왜 README.md, pyproject.toml src tests를 다 넘기는거야?',
)


@pytest.mark.parametrize('text', [
    *QUESTIONS,
    'Codex가 뭐야?',
    'codex에게 맡기면 뭐가 좋아?',
    'codex에게 조사를 맡긴 이유를 알려줘',
    'codex에게 README를 검토시킨 이유 설명해줘',
    'codex에게 README.md 해줘',
    'codex에게 bread 해줘',
    'codex에게 preview 해줘',
    'codex로 분석 가능해?',
    'codex로 분석할 수 있어?',
    'codex 분석해줘',
    'mycodex로 분석해줘',
    'codex로 조사하는 거야?',
    'Codex를 써서 이 프로젝트를 찾아봐',  # outside the requested ending allowlist
])
def test_questions_mentions_and_filename_substrings_do_not_delegate(text):
    assert ExplicitCodexRoutingPolicy().route(text) is None


@pytest.mark.parametrize('text', [
    'codex로 이 함수 원인 분석해줘',
    'codex로 분석해',
    '코덱스에게 테스트 실패 이유 조사시켜',
    'Codex 써서 README 확인해봐',
    '코드엑스한테 README 검토해줄래?',
    'codex를 이용해서 README 조사해줘!',
    'codex를 통해 README 검토 부탁.',
    'codex에게 이 함수 분석 맡겨',
    'codex에게 README read 해줘',
    'codex에게 README review 해줘',
    'codex에게 원인 analyze 해줘',
    'codex에게 원인 inspect 해줘',
    'codex에게 원인 investigate 해줘',
])
def test_explicit_requests_delegate_exact_task(text):
    request = ExplicitCodexRoutingPolicy().route(text)
    assert request is not None and request.task == text


def test_approval_prompt_snapshot(monkeypatch):
    monkeypatch.setattr('companion.text_host.uuid4', lambda: SimpleNamespace(hex='turn-1'))
    _, _, output_stream = run_host()
    expected = (
        '[외부 Codex / 읽기 전용]\n'
        '보낼 요청: Codex로 README 확인해줘\n'
        '읽기 범위(고정 허용 목록, 겨울이가 고른 게 아님): '
        'README.md, pyproject.toml, src/, tests/\n'
        'Codex는 이 안에서만 읽고 답만 돌려줘. 파일 변경·인터넷 없음.\n'
        "이 요청과 범위의 외부 전송을 승인하려면 '승인' 입력 (나머지는 취소)> "
    )
    assert expected in output_stream.getvalue()
    assert '/home/' not in output_stream.getvalue()


def test_self_model_operation_description_is_six_fixed_lines_before_variable_tail(tmp_path):
    snapshot = SelfModelSnapshot('겨울이', 'fixture', (), ())
    block = snapshot.system_message().split('동작 방식(사실만 말해):', 1)[1]
    assert block.splitlines() == [
        '',
        '- 천우가 한 말을 대화가 끝난 뒤 사실 문장으로 정리해 기억하고, '
        '다음 대화에서 그걸 배경으로 쓴다.',
        '- 인터넷·클라우드 검색은 없다. 아는 건 천우가 말한 것과 저장된 기억뿐이다.',
        '- 코드 확인은 천우가 "코덱스로 …해줘"처럼 명시적으로 시킬 때만, '
        "천우가 터미널에서 '승인'한 뒤 Codex가 고정 읽기 범위"
        '(README.md, pyproject.toml, src/, tests/) 안에서만 읽고 답을 돌려준다. '
        '범위는 겨울이가 정하지 않는다.',
        '- 날짜·시간이 있는 약속은 일정으로 저장할 수 있고, '
        '저장 결과는 실제 기록으로만 말한다.',
        '- 모르는 동작 원리는 지어내지 말고 모른다고 말해.',
    ]
    h = Harness(tmp_path)
    h.runtime.core._self_model_provider = SimpleNamespace(snapshot=lambda: snapshot)
    first = h.runtime.core._prepare('어떤 방식으로 정보를 처리해?').request
    second = h.runtime.core._prepare('읽기 범위를 왜 그렇게 정했어?').request
    position = next(i for i, m in enumerate(first.messages)
                    if '동작 방식(사실만 말해)' in m.content)
    assert first.messages[:position + 1] == second.messages[:position + 1]
    assert position < next(i for i, m in enumerate(first.messages)
                           if m.content.startswith('일정 날짜 기준'))
    assert position < len(first.messages) - 1
    assert first.messages[-1].content == first.prompt


@pytest.mark.parametrize('draft', [
    '읽기 범위가 넓어 보여서 취소했다고 했을 때, 내가 그걸 전부 보내려는 건 '
    '네가 원하지 않았던 거잖아. 지금은 네가 말한 대로, 내가 전부 보여주지 않아도 돼. '
    '너의 말에 맞춰서, 필요한 걸 골라서 말할게.',  # actual turn 557 draft, not a quality example
    '네가 취소했다고 했을 때 읽기 범위가 넓어서 그랬다는 거구나.',
    '저장 안 했어.',
    '일정은 아직 없어. 네가 취소했다고 했을 때 범위가 넓다는 뜻이었구나.',
    '약속은 아직 없고, 네가 취소했다고 했을 때 범위가 넓다는 뜻이었구나.',
])
def test_non_schedule_completion_language_keeps_draft(tmp_path, draft):
    h = Harness(tmp_path)
    final, record = h.turn(
        '읽기 범위가 너무 방대해보여서 취소한건데 왜 저것들을 전부 보내야 하는거야?',
        draft=draft,
    )
    assert final == draft and record['discard_reason'] is None
    assert record['calls'] == 1 and h.count_history() == 0


@pytest.mark.parametrize('draft', ['내일 약속 저장했어.', '일정 취소했어.', '회의 등록했어.'])
def test_unreceipted_schedule_completion_is_still_blocked(tmp_path, draft):
    h = Harness(tmp_path)
    final, record = h.turn('코덱스 범위를 설명해줘', draft=draft)
    assert final == '이번 대화에서는 일정을 저장하거나 바꾸지 않았어.'
    assert record['discard_reason'] == 'completion_without_receipt'
    assert h.count_history() == 0


def test_proposal_without_receipt_still_blocks_completion_without_schedule_noun(tmp_path):
    h = Harness(tmp_path)
    turn = h.runtime.core._prepare('코덱스에 대해 말해줘').schedule_turn
    turn.proposal = {'operation': 'create'}
    assert h.dialogue.display(turn,
        '저장했어.') == '이번 대화에서는 일정을 저장하거나 바꾸지 않았어.'
    assert turn.discard_reason == 'completion_without_receipt'


class DecliningWorker:
    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        raise WorkerApprovalDeclined('fixture decline')


class OneCallModel:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        assert len(self.requests) == 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return ChatResult(self.answer)


ACK = '알겠어, 코덱스엔 안 보낼게. 내가 아는 선에서 말해볼게.'


@pytest.mark.parametrize('text', QUESTIONS)
def test_actual_questions_reach_local_model_without_worker_approval(tmp_path, text):
    h = Harness(tmp_path)
    draft = '읽기 범위는 고정 허용 목록이야. 내가 정하지 않아.'
    model, worker = OneCallModel(output(draft)), DecliningWorker()
    h.runtime.core._chat_model, h.runtime.core._read_only_worker = model, worker
    assert h.runtime.core.respond_to_text(text).text == draft
    assert worker.requests == [] and len(model.requests) == 1


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('structured', [False, True])
def test_decline_answers_original_request_once_and_persists_final(tmp_path, streaming, structured):
    draft = '읽기 범위는 Host의 고정 허용 목록이야. 내가 고른 범위가 아니야.'
    model = OneCallModel(output(draft) if structured else draft)
    worker = DecliningWorker()
    if structured:
        h = Harness(tmp_path)
        core = h.runtime.core
        core._chat_model, core._read_only_worker = model, worker
    else:
        core = CompanionCore(model, InMemoryConversationRepository(), read_only_worker=worker)
    task = 'codex로 읽기 범위 확인해줘'
    emitted = []
    response = (core.respond_to_text_streaming(task, emitted.append) if streaming
                else core.respond_to_text(task))
    assert response.text == ACK + ' ' + draft
    assert len(model.requests) == len(worker.requests) == 1
    request = model.requests[0]
    assert request.prompt == task and request.messages[-1].content == task
    assert 'Codex는 실행되지 않았고' in request.messages[-2].content
    assert response.worker_error and not response.worker_evidence
    assert core._conversation_repository.list_messages()[-1].content == response.text
    if streaming:
        assert ' '.join(emitted) == response.text
    if structured:
        assert h.count_history() == 0
        assert h.observations()[-1]['calls'] == 1


@pytest.mark.parametrize('answer', [
    '', '{broken', AdapterUnavailableError('incomplete reply'), TimeoutError('budget'),
    output('저장했어.', 'create', {'title': '약속', 'date': '내일'}),
])
def test_decline_bad_or_unavailable_answer_uses_only_ack_without_retry(tmp_path, answer):
    h = Harness(tmp_path)
    model = OneCallModel(answer)
    h.runtime.core._chat_model = model
    h.runtime.core._read_only_worker = DecliningWorker()
    response = h.runtime.core.respond_to_text('codex로 읽기 범위 확인해줘')
    assert response.text == ACK and len(model.requests) == 1
    assert h.count_history() == 0


@pytest.mark.parametrize('budget', ['calls', 'tokens'])
def test_decline_exhausted_budget_skips_model(tmp_path, budget):
    h = Harness(tmp_path)
    core = h.runtime.core
    model = OneCallModel('must not be called')
    core._chat_model = model
    core._read_only_worker = DecliningWorker()
    turn = core._prepare('codex로 읽기 범위 확인해줘')
    if budget == 'calls':
        turn.schedule_turn.calls = 2
    else:
        turn = replace(turn, request=replace(turn.request, max_tokens=0))
    assert core._respond_with_worker(turn).text == ACK
    assert model.requests == []


def test_queued_acknowledgement_does_not_call_local_model():
    class QueuedWorker:
        def execute(self, request):
            raise WorkerQueued('fixture queued')

    model = OneCallModel('must not be called')
    core = CompanionCore(model, InMemoryConversationRepository(), read_only_worker=QueuedWorker())
    assert core.respond_to_text('codex로 코드 분석해줘').text == (
        '코덱스한테 넘겼어. 결과 오면 바로 말해줄게.')
    assert model.requests == []
