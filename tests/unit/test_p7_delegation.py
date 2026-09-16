"""Model-owned proposals with reply-first, Host-approved background execution."""

from io import StringIO
import json
from types import SimpleNamespace

from configs.dialogue import delegation
import pytest
from test_reply_streaming import StreamingModel
from test_schedule_dialogue import Harness, output
from test_text_session import wire

from companion.dialogue.schedule_dialogue import (
    CODEX_INSTRUCTION,
    INSTRUCTION,
    OUTPUT_SCHEMA,
    parse_output,
)
from companion.errors import AdapterUnavailableError
from companion.owned_work import OwnedWorkReceipt
from companion.session_wire import SessionProtocolError
from companion.text_child import run
from companion.text_host import audited_execution, session
from companion.text_jobs import ProcessWorkerQueue
from companion.work_orchestration import WorkRequest
from companion.worker import ReadOnlyWorkerRequest, WorkerUnavailableError

TASK = '입력 처리 코드를 읽어 두 번 묻는 원인을 조사해줘.'
WHY = '실제 입력 흐름을 확인해야 해.'
CODEX = dict(task=TASK, why=WHY)
DRAFT = '입력 흐름을 봐야 알겠어. 코덱스 결과는 나중에 알려줄게.'


def proposed(codex=CODEX, draft=DRAFT, **kwargs):
    payload = json.loads(output(draft, **kwargs))
    return json.dumps(dict(schedule_proposal=payload['schedule_proposal'],
                           codex_request=codex, reply_draft=draft), ensure_ascii=False)


def test_schema_order_strict_bounds_and_fixed_instruction():
    assert list(OUTPUT_SCHEMA['properties']) == [
        'schedule_proposal', 'codex_request', 'reply_draft',
    ]
    assert OUTPUT_SCHEMA['required'] == ['schedule_proposal', 'codex_request', 'reply_draft']
    schema = OUTPUT_SCHEMA['properties']['codex_request']['anyOf'][1]
    assert not schema['additionalProperties'] and schema['required'] == ['task', 'why']
    for key, limit in [('task', 400), ('why', 120)]:
        # No `pattern`: llama.cpp only accepts anchored patterns and returned HTTP 400.
        assert schema['properties'][key] == dict(type='string', minLength=1, maxLength=limit)
    assert INSTRUCTION.endswith(CODEX_INSTRUCTION)
    assert CODEX_INSTRUCTION.count('천우:') == 7
    assert delegation.REQUIRE_APPROVAL is True


@pytest.mark.parametrize('codex', [None, CODEX, dict(task='가' * 400, why='나' * 120)])
def test_parser_accepts_null_and_exact_objects(codex):
    draft, schedule, request = parse_output(proposed(codex))
    assert draft == DRAFT and schedule is None
    assert request == (ReadOnlyWorkerRequest(**codex) if codex else None)
    assert parse_output(output('일반 대화'))[2] is None


@pytest.mark.parametrize('bad', [
    True, 1, '', [], {}, {'task': TASK}, {'why': WHY}, dict(CODEX, extra='x'),
    dict(CODEX, task=None), dict(CODEX, why=[]), dict(CODEX, task=''), dict(CODEX, why=''),
    dict(CODEX, task=' \n\t'), dict(CODEX, why=' \n\t'),
    dict(CODEX, task='가' * 401), dict(CODEX, why='나' * 121),
])
def test_invalid_codex_uses_existing_bounded_retry(tmp_path, bad):
    with pytest.raises(ValueError, match='codex_request'):
        parse_output(proposed(bad))
    h = Harness(tmp_path)
    h.model.queue.extend([proposed(bad), proposed()])
    response = h.runtime.core.respond_to_text('테스트 실패 원인 봐줘')
    assert response.text == DRAFT and response.worker_request is None
    observation = h.observations()[-1]
    assert observation['calls'] == 2 and len(observation['validation_errors']) == 1
    assert not observation['codex_requested'] and 'codex_task' not in observation
    assert h.runtime.close()


@pytest.mark.parametrize('prompt', ['codex로 조사해줘', '테스트 실패 원인 봐줘'])
@pytest.mark.parametrize('codex', [None, CODEX])
def test_model_output_alone_controls_delegation_and_normal_reply_is_saved(tmp_path, prompt, codex):
    h = Harness(tmp_path)
    h.model.queue.append(proposed(codex))
    response = h.runtime.core.respond_to_text(prompt)
    assert response.text == DRAFT
    assert response.worker_request == (ReadOnlyWorkerRequest(**codex) if codex else None)
    assert h.runtime.resources.repository.list_messages()[-1].content == DRAFT
    record = h.observations()[-1]
    assert record['calls'] == 1 and record['codex_requested'] == bool(codex)
    assert record.get('codex_task') == (TASK if codex else None)
    assert h.runtime.close()


@pytest.mark.parametrize('first_codex', [None, CODEX])
def test_schedule_and_worker_coexist_but_receipt_repair_cannot_add_or_replace_request(
    tmp_path, first_codex,
):
    h = Harness(tmp_path)
    h.model.queue.extend([
        proposed(first_codex, '금요일 모임이야.', operation='create',
                 fields={'title': '모임', 'date': '내일'}),
        proposed(dict(task='두 번째 요청', why='무시해야 해.'), '내일 모임 일정에 넣어뒀어.'),
    ])
    response = h.runtime.core.respond_to_text('내일 모임 일정에 넣어줘. 테스트 실패 원인도 봐줘.')
    expected = ReadOnlyWorkerRequest(**first_codex) if first_codex else None
    assert response.worker_request == expected
    assert response.text == '내일 모임 일정에 넣어뒀어.'
    assert h.count_history() == 1 and len(h.repo.query()) == 1
    assert h.observations()[-1]['calls'] == 2
    assert h.runtime.close()


def test_incomplete_first_call_cannot_delegate_from_retry(tmp_path):
    h = Harness(tmp_path)
    h.model.queue.extend([AdapterUnavailableError('incomplete reply'), proposed()])
    response = h.runtime.core.respond_to_text('테스트 실패 원인 봐줘')
    assert response.worker_request is None and len(h.model.requests) == 2
    assert h.runtime.close()


@pytest.mark.parametrize('disposition, notice', [
    ({'kind': 'worker_queued', 'job_id': 'job'}, '코덱스 요청을 접수했어. 결과가 오면 알려줄게.'),
    ({'kind': 'worker_declined'}, '코덱스 요청을 취소했어.'),
    ({'kind': 'worker_error'}, '코덱스 요청을 처리하지 못했어.'),
])
def test_child_stream_reply_request_notification_order_and_no_second_answer(
    tmp_path, monkeypatch, disposition, notice,
):
    h = Harness(tmp_path)
    model = StreamingModel()
    model.queue.append(proposed())
    h.runtime.core._chat_model = model
    monkeypatch.setattr('companion.user_cli.DATA', tmp_path)
    monkeypatch.setattr('companion.text_child.build_text_runtime', lambda *a, **kw: h.runtime)
    stream = StringIO()
    run(['--dialogue-policy', 'companion'], wire(
        dict(kind='turn', id='one', text='테스트 실패 원인 봐줘'), dict(disposition, id='one'),
        dict(kind='close'),
    ), stream)
    frames = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert frames[0]['kind'] == 'ready'
    assert all(f['kind'] == 'partial' for f in frames[1:-3])
    assert ''.join(f['text'] for f in frames[1:-3]) == DRAFT
    assert frames[-3:] == [
        dict(kind='reply', id='one', text=DRAFT, worker_pending=True),
        dict(kind='worker_request', id='one', **CODEX),
        dict(kind='notification', id='one', text=notice),
    ]
    assert model.stream_calls == 1
    messages = h.runtime.resources.repository.list_messages()
    assert [m.content for m in messages] == ['테스트 실패 원인 봐줘', DRAFT, notice]


def host_frames():
    return [dict(kind='ready', name='겨울이'),
            dict(kind='partial', id='turn', text=DRAFT),
            dict(kind='reply', id='turn', text=DRAFT, worker_pending=True),
            dict(kind='worker_request', id='turn', **CODEX),
            dict(kind='notification', id='turn', text='접수 상태야.')]


@pytest.mark.parametrize('mode', ['approved', 'declined', 'auto', 'error'])
def test_host_reply_before_approval_and_background_status(monkeypatch, mode):
    monkeypatch.setattr('companion.text_host.uuid4', lambda: SimpleNamespace(hex='turn'))
    monkeypatch.setattr(delegation, 'REQUIRE_APPROVAL', mode != 'auto')
    calls, out, child_in = [], StringIO(), StringIO()

    class Jobs:
        def ready(self):
            return ()

        def submit(self, request, *, approval_mode='manual'):
            assert DRAFT in out.getvalue()  # Already visible at submission.
            calls.append((request.user_text, approval_mode))
            if mode == 'error':
                raise WorkerUnavailableError('busy')
            return OwnedWorkReceipt('job')

    approval = '' if mode == 'auto' else ('아니\n' if mode == 'declined' else '승인\n')
    session(child_in, wire(*host_frames()), StringIO('테스트 실패 원인 봐줘\n'+approval+'/exit\n'),
            out, lambda _: pytest.fail('must queue'), scope='고정 범위', background=Jobs())
    sent = [json.loads(line) for line in child_in.getvalue().splitlines()]
    assert sent[1]['kind'] == {'declined': 'worker_declined', 'error': 'worker_error'}.get(
        mode, 'worker_queued')
    assert calls == ([] if mode == 'declined' else [(TASK, 'auto' if mode == 'auto' else 'manual')])
    assert out.getvalue().count('겨울이>') == 1
    if mode == 'auto':
        assert '승인' not in out.getvalue()
        assert '[알림]' not in out.getvalue()
        assert f'[코덱스에 물어보는 중] {TASK} — 이유: {WHY}' in out.getvalue()
    else:
        assert out.getvalue().index(DRAFT) < out.getvalue().index('외부 전송을 승인')
        assert f'이유: {WHY}' in out.getvalue()


@pytest.mark.parametrize('bad', [
    dict(kind='worker_request', id='other', **CODEX),
    dict(kind='worker_request', id='turn', task=TASK),
    dict(kind='worker_request', id='turn', task=TASK, why=' '),
    dict(kind='worker_request', id='turn', task='가'*401, why=WHY),
    dict(kind='partial', id='turn', text='다시'),
    dict(kind='reply', id='turn', text='다시'),
])
def test_post_reply_invalid_frames_never_execute(monkeypatch, bad):
    monkeypatch.setattr('companion.text_host.uuid4', lambda: SimpleNamespace(hex='turn'))
    with pytest.raises(SessionProtocolError):
        session(StringIO(), wire(*host_frames()[:3], bad), StringIO('확인해줘\n승인\n'),
                StringIO(), lambda _: pytest.fail('invalid request'), scope='고정 범위')


def test_auto_mode_reaches_detached_job_and_durable_approval_audit(tmp_path, monkeypatch):
    import sys

    from companion import text_jobs

    commands = []
    jobs = ProcessWorkerQueue(tmp_path/'jobs.sqlite', tmp_path, tmp_path/'audit',
                              spawn=lambda command, **kwargs: commands.append(command))
    receipt = jobs.submit(WorkRequest(TASK), approval_mode='auto')
    command = commands[0]
    assert command[command.index('--approval-mode')+1] == 'auto'

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, packet):
            paths = list((tmp_path/'audit').glob('*.jsonl'))
            assert len(paths) == 1
            approved = json.loads(paths[0].read_text())
            assert approved['kind'] == 'approved' and approved['mode'] == 'auto'
            assert approved['packet']['objective'] == TASK
            raise WorkerUnavailableError('synthetic failure after audit')

    monkeypatch.setattr(text_jobs, 'CodexTypedProjectWorker', Worker)
    monkeypatch.setattr(sys, 'argv', command[2:])
    assert text_jobs.main() == 0
    assert jobs.repository.status(receipt.id) == 'failed'


def test_manual_audit_format_retained(tmp_path):
    class Worker:
        def execute(self, packet):
            raise WorkerUnavailableError('fixture')

    with pytest.raises(WorkerUnavailableError):
        audited_execution(Worker(), TASK, tmp_path)
    records = [json.loads(line) for line in next(tmp_path.glob('*.jsonl')).read_text().splitlines()]
    assert set(records[0]) == {'kind', 'at', 'packet'}


@pytest.mark.parametrize('first_codex', [None, CODEX])
@pytest.mark.parametrize('repair_kind', ['repeat', 'claim'])
def test_regeneration_and_claim_repair_ignore_second_request(tmp_path, first_codex, repair_kind):
    from test_repeated_reply import FRESH, REPEATED, seed

    from companion.grounding import ClaimViolation

    h = Harness(tmp_path)
    core = h.runtime.core
    if repair_kind == 'repeat':
        seed(h.runtime.resources.repository, REPEATED)
    else:
        class Gate:
            def violations(self, text, evidence):
                return ((ClaimViolation('fabricated_memory', REPEATED),)
                        if text == REPEATED else ())

            def blocking_violations(self, violations):
                return violations

            def repair_instruction(self, violations, evidence):
                return '근거 없는 내용을 고쳐.'

        core._response_claim_gate = Gate()
    h.model.queue.extend([proposed(first_codex, REPEATED), proposed(CODEX, FRESH)])
    response = core.respond_to_text('다른 이야기 해줘')
    assert response.text == FRESH
    expected = ReadOnlyWorkerRequest(**first_codex) if first_codex else None
    assert response.worker_request == expected
    assert h.observations()[-1]['calls'] == 2
    assert h.runtime.close()
