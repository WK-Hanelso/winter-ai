"""P2 factual acceptance via actual Core, isolated SQLite and fake/capture models."""

from datetime import datetime
import json
from pathlib import Path
import sqlite3

import pytest

from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime
from companion.contracts import ChatResult
from companion.dialogue.schedule_dialogue import ScheduleOutputError, parse_output, receipt_text
from companion.schedules import KST, ChangeReceipt, Schedule

SCENES = json.loads((Path(__file__).parents[1] / 'fixtures/schedule-p2-scenes.json').read_text())


class FixedClock:
    def __init__(self):
        self.value = datetime(2026, 9, 14, 9, tzinfo=KST)
        self.calls = 0

    def now(self):
        self.calls += 1
        return self.value


def output(draft='응, 알겠어.', operation=None, fields=None, target=None, cue=None):
    proposal = None if operation is None else dict(operation=operation, fields=fields or {},
                                                  target=target or {}, cue=cue)
    return json.dumps(dict(reply_draft=draft, schedule_proposal=proposal), ensure_ascii=False)


class Model:
    def __init__(self):
        self.queue = []
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        assert request.response_format['type'] == 'json_schema'
        assert request.response_format['schema'] == request.response_format['json_schema']['schema']
        if self.queue:
            value = self.queue.pop(0)
            if isinstance(value, Exception):
                raise value
            return ChatResult(value)
        # The fake's second answer reads the ACTUAL receipt injected by Core.
        message = next(m for m in reversed(request.messages)
                       if m.content.startswith('확인된 일정'))
        receipt = json.loads(message.content.split('\n', 1)[1])['receipt']
        if receipt['status'] == 'needs_clarification':
            if receipt['reason_code'] in {'date_mismatch', 'ungrounded_date', 'ambiguous_date',
                                         'past_date', 'missing_date'}:
                return ChatResult(output(receipt['clarification']))
            names = ' / '.join(f"{s['date']} {s['participants_text']} {s['title']}"
                               for s in receipt['schedules'])
            return ChatResult(output(f'{names} 중 어느 모임을 바꿀까?'))
        receipt['schedules'] = tuple(Schedule(**s) for s in receipt['schedules'])
        return ChatResult(output(receipt_text(ChangeReceipt(**receipt))))


class Harness:
    def __init__(self, path):
        self.path = path
        self.config = TextRuntimeConfig(dialogue_policy='companion',
            conversation_db=path / 'conversations.sqlite', knowledge_db=path / 'knowledge.sqlite')
        self.model, self.clock = Model(), FixedClock()
        self.restart()

    def restart(self):
        self.runtime = build_text_runtime(self.config, chat_model=self.model)
        self.dialogue = self.runtime.core._schedule_dialogue
        self.dialogue.service.clock = self.clock
        self.repo = self.dialogue.service.repository

    def turn(self, text, operation=None, fields=None, target=None, draft='응, 알겠어.',
             cue=None, streaming=False):
        self.model.queue.append(output(draft, operation, fields, target, cue or text))
        before = len(self.model.requests)
        emitted = []
        response = (self.runtime.core.respond_to_text_streaming(text, emitted.append)
                    if streaming else self.runtime.core.respond_to_text(text))
        assert len(self.model.requests) - before <= 2
        if streaming:
            assert emitted and '{"reply_draft"' not in ''.join(emitted)
        record = self.observations()[-1]
        assert record['calls'] == len(self.model.requests) - before
        assert record['final'] == response.text
        assert self.runtime.resources.repository.list_messages()[-1].content == response.text
        return response.text, record

    def observations(self):
        return [json.loads(line) for line in
                (self.path / 'schedule_observations.jsonl').read_text().splitlines()]

    def count_history(self):
        with sqlite3.connect(self.path / 'knowledge.sqlite') as conn:
            return conn.execute('SELECT count(*) FROM change_history').fetchone()[0]


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def test_scene_1_create_correct_topic_query(h):
    turns = SCENES[0]
    h.turn(turns[0], 'create', dict(title='독서 모임', date='내일', time='오후 3시',
                                  place='시청 도서관', participants='민수'), streaming=True)
    original = h.repo.query()[0]
    assert (original.date, original.time, original.revision) == ('2026-09-15', '15:00', 1)
    final, record = h.turn(turns[1], 'update', {'date': '금요일'}, {'title_hint': '독서 모임'},
                           draft='바꿔놨어. 토요일에 보면 되겠다.')
    item = h.repo.query()[0]
    assert (item.id, item.date, item.revision, item.status) == (
        original.id, '2026-09-18', 2, 'changed')
    assert (item.time, item.place, item.participants_text) == ('15:00', '시청 도서관', '민수')
    assert '토요일' not in final and record['receipt']['status'] == 'applied'
    h.turn(turns[2], draft='한 장 읽으면 상상력이 먼저 퇴근하네ㅋㅋ')
    final, record = h.turn(turns[3], 'query', target={'title_hint': '독서 모임'})
    assert all(value in final for value in ('2026-09-18', '15:00', '시청 도서관', '민수'))
    assert record['calls'] == 2 and h.count_history() == 2


def test_scene_2_ambiguous_target_and_followup(h):
    turns = SCENES[1]
    h.turn(turns[0], 'create', dict(title='작업 모임', date='내일', time='오전 10시',
                                  place='북쪽 카페', participants='지수'))
    h.turn(turns[1], 'create', dict(title='작업 모임', date='금요일', time='오후 4시',
                                  place='남쪽 카페', participants='준호'))
    before = h.repo.query()
    final, record = h.turn(turns[2], 'update', {'place': '동쪽 카페'}, {'title_hint': '작업 모임'},
                           draft='바꿔놨어. 거기서 만나면 되겠네.')
    assert record['receipt']['status'] == 'needs_clarification' and record['calls'] == 2
    assert '바꿔놨어' not in final and '거기서' not in final
    assert all(v in final for v in ('2026-09-15', '2026-09-18', '지수', '준호'))
    assert h.repo.query() == before and h.count_history() == 2
    selected = next(s for s in before if s.participants_text == '준호')
    _, record = h.turn(turns[3], 'update', {'place': '동쪽 카페'}, {'schedule_id': selected.id})
    item = h.repo.get(selected.id)
    assert item.place == '동쪽 카페' and item.revision == 2
    assert next(s for s in h.repo.query() if s.id != item.id).revision == 1
    history = h.repo.history(selected.id)[-1]
    request = json.loads(history['change_json'])['command']['request']
    assert request['original_source_message_id'] == 5
    assert item.source_message_id == 7 and h.count_history() == 3


def test_scene_3_cancel_is_local_and_visible(h):
    turns = SCENES[2]
    h.turn(turns[0], 'create',
           dict(title='미용실 예약', date='내일', time='오전 11시', place='중앙 미용실'))
    final, _ = h.turn(turns[1], 'cancel', target={'title_hint': '미용실 예약'},
                      draft='미용실에 전화해서 예약 취소했어.')
    assert '전화' not in final and '로컬 일정만 취소했어' in final
    h.turn(turns[2], draft='오늘은 쉬는 쪽으로 마음이 기우네.')
    final, record = h.turn(turns[3], 'query', {'date_from': '내일', 'date_to': '내일'})
    assert '취소됨' in final and record['receipt']['schedules'][0]['status'] == 'cancelled'
    assert h.repo.query()[0].revision == 2 and h.count_history() == 2


def test_scene_4_unknown_time_stays_null(h):
    turns = SCENES[3]
    h.turn(turns[0], 'create', {'title': '동네 전시', 'date': '내일'})
    h.turn(turns[1], 'update', {'place': '강변 갤러리'}, {'title_hint': '동네 전시'})
    h.turn(turns[2], draft='제목부터 해설이 필요하네ㅋㅋ')
    final, record = h.turn(turns[3], 'query', target={'title_hint': '동네 전시'})
    assert '시간 미정' in final and '강변 갤러리' in final
    item = h.repo.query()[0]
    assert item.time is None and item.revision == 2 and item.source_message_id == 3
    assert record['receipt']['schedules'][0]['time'] is None and h.count_history() == 2


def test_scene_5_restart_new_clock_same_schedule(h):
    turns = SCENES[4]
    h.turn(turns[0], 'create', dict(title='산책', date='내일', time='오후 7시',
                                  place='서쪽 공원', participants='수진'))
    h.turn(turns[1], 'update', {'time': '오후 8시'}, {'title_hint': '산책'})
    before = h.repo.query()
    h.turn(turns[2], draft='잘 자, 다음에 얘기하자.')
    assert h.runtime.close()
    h.clock.value = datetime(2026, 9, 15, 9, tzinfo=KST)
    h.restart()
    final, record = h.turn(turns[3], 'query', {'date_from': '오늘', 'date_to': '오늘'})
    assert h.repo.query() == before and h.count_history() == 2
    assert all(v in final for v in ('2026-09-15', '20:00', '서쪽 공원', '수진'))
    assert record['calls'] == 2


def test_scene_6_no_schedule_writes_or_second_call(h):
    for text in SCENES[5]:
        _, record = h.turn(text, draft='응, 듣고 있어.')
        assert record['proposal'] is None and record['receipt'] is None and record['calls'] == 1
    assert not h.repo.query() and h.count_history() == 0
    assert h.clock.calls == 4


@pytest.mark.parametrize('cue', ['없는 원문 구절', '일', '', None, 123, 'x' * 161])
def test_invalid_cue_uses_current_field_grounding(h, cue):
    h.model.queue.append(output('바꿔놨어. 이제 거기로 가면 되겠네.', 'create',
                                 {'title': '전시', 'date': '내일'}, cue=cue))
    final = h.runtime.core.respond_to_text('내일 전시 일정에 넣어줘').text
    record = h.observations()[-1]
    assert record['receipt']['status'] == 'applied' and record['calls'] == 1
    assert final and h.count_history() == 1


@pytest.mark.parametrize('raw', ['바꿔놨어', '```json\n{}\n```', '{', '[]',
                                 '{"reply_draft":"a","reply_draft":"b","schedule_proposal":null}'])
def test_json_failure_retries_once_never_publishes_raw(h, raw):
    h.model.queue.extend([raw, output('안녕!')])
    assert h.runtime.core.respond_to_text('안녕').text == '안녕!'
    assert h.observations()[-1]['calls'] == 2
    assert len(h.runtime.resources.repository.list_messages()) == 2


def test_two_invalid_jsons_use_error_path_and_no_assistant_history(h):
    h.model.queue.extend(['바꿔놨어', '{'])
    with pytest.raises(ScheduleOutputError, match='답변을 완성하지 못했어'):
        h.runtime.core.respond_to_text('일정 추가해줘')
    record = h.observations()[-1]
    assert record['calls'] == 2 and record['final'] is None and record['error']
    assert len(h.runtime.resources.repository.list_messages()) == 1 and h.count_history() == 0


def test_retry_plus_query_does_not_make_third_call(h):
    h.model.queue.extend(
        ['bad', output('조회했어', 'query', target={'title_hint': '전시'}, cue='전시')])
    final = h.runtime.core.respond_to_text('전시 일정 알려줘').text
    assert final == '해당하는 일정은 없어.' and h.observations()[-1]['calls'] == 2


@pytest.mark.parametrize('status,reason,expected', [
    ('rejected', 'revision_conflict', '반영하지 못했어'),
    ('failed', 'database_error', '반영하지 못했어'),
    ('failed', 'commit_unconfirmed', '저장됐는지 지금 확인하지 못했어'),
])
def test_failed_receipts_discard_success_presupposition(h, monkeypatch, status, reason, expected):
    def fail(command, **kwargs):
        return ChangeReceipt(status, command.operation, command.command_id,
                             command.source_message_id, reason_code=reason)
    monkeypatch.setattr(h.dialogue.service, 'execute', fail)
    final, record = h.turn('내일 전시 저장해줘', 'create', {'title': '전시', 'date': '내일'},
                           draft='바꿔놨어. 그때 가면 되겠네.')
    assert expected in final and '그때' not in final and '바꿔놨어' not in final
    assert record['calls'] == 1 and h.count_history() == 0


def test_null_proposal_cannot_claim_write(h):
    final, record = h.turn('그냥 수다 떨자', draft='바꿔놨어. 거기서 만나.')
    assert '바꿔놨어' not in final and '거기서' not in final
    assert record['discard_reason'] == 'completion_without_receipt'


def test_second_proposal_never_executes(h):
    h.model.queue.extend([
        output('찾아볼게', 'query', target={'title_hint': '전시'}, cue='전시'),
        output('저장했어', 'create', {'title': '전시', 'date': '내일'}, cue='전시'),
    ])
    h.runtime.core.respond_to_text('전시 알려줘')
    assert h.count_history() == 0 and h.observations()[-1]['calls'] == 2


def test_legacy_and_none_disable_schedule_runtime(tmp_path):
    class PlainModel:
        def generate(self, request):
            assert request.response_format is None
            return ChatResult('안녕!')
    path = tmp_path / 'knowledge.sqlite'
    for config in (TextRuntimeConfig(dialogue_policy='legacy', knowledge_db=path),
                   TextRuntimeConfig(dialogue_policy='companion')):
        runtime = build_text_runtime(config, chat_model=PlainModel())
        assert runtime.core._schedule_dialogue is None
        assert runtime.core.respond_to_text('안녕').text == '안녕!'
    assert not path.exists()


def test_requires_persistent_source(tmp_path):
    with pytest.raises(RuntimeError, match='persistent conversation'):
        build_text_runtime(TextRuntimeConfig(dialogue_policy='companion',
                           knowledge_db=tmp_path / 'knowledge.sqlite'), chat_model=Model())
    assert not (tmp_path / 'knowledge.sqlite').exists()


def test_empty_block_omitted_and_schedule_block_after_fixed_instructions(h):
    h.turn('내일 전시 저장해줘', 'create', {'title': '전시', 'date': '내일'})
    assert not any(m.content.startswith('확인된 일정') for m in h.model.requests[0].messages)
    h.turn('안녕', draft='안녕!')
    messages = h.model.requests[-1].messages
    index = next(i for i, m in enumerate(messages) if m.content.startswith('확인된 일정'))
    fixed = next(i for i, m in enumerate(messages) if m.content.startswith('JSON 객체 하나만'))
    assert index > fixed and messages[index].role == 'system' and messages[-1].role == 'user'
    bundle = json.loads(messages[index].content.split('\n', 1)[1])
    assert bundle['schedules'][0]['date'] == '2026-09-15'


def test_context_is_bounded_and_cancelled_status_retained(h):
    for i in range(10):
        h.turn('내일 전시 저장해줘', 'create', {'title': f'전시{i}', 'date': '내일'})
    h.turn('전시0 취소해줘', 'cancel', target={'title_hint': '전시0'})
    state = h.dialogue.begin(99)
    assert len(state.context.schedules) == 8
    h.clock.value = datetime(2026, 11, 1, 9, tzinfo=KST)
    assert not h.dialogue.begin(100).context.schedules


def test_schema_ignores_and_records_model_owned_command_fields():
    value = json.loads(output('응', 'create', {'title': '전시', 'date': '내일'}, cue='전시'))
    value['schedule_proposal']['command_id'] = 'model-owned'
    warnings = []
    _, proposal = parse_output(json.dumps(value), warnings=warnings)
    assert 'command_id' not in proposal
    assert warnings == ['ignored_key:proposal.command_id']


def test_applied_grounded_draft_is_preserved_verbatim(h):
    draft = '전시 일정에 넣어뒀어.'
    final, record = h.turn('내일 전시 일정에 넣어줘', 'create',
                           {'title': '전시', 'date': '내일'}, draft=draft)
    assert final == draft and record['discard_reason'] is None and record['calls'] == 1
    draft = '전시 날짜만 금요일로 바꿔놨어.'
    final, record = h.turn('전시 날짜만 금요일로 바꿔줘', 'update', {'date': '금요일'},
                           {'title_hint': '전시'}, draft=draft)
    assert final == draft and record['discard_reason'] is None and record['calls'] == 1


def test_ambiguous_second_answer_cannot_leak_success(h):
    for date in ('내일', '금요일'):
        h.turn('모임 저장해줘', 'create', {'title': '모임', 'date': date})
    h.model.queue.extend([
        output('바꿔놨어', 'update', {'place': '카페'}, {'title_hint': '모임'}, '장소'),
        output('2026-09-15와 2026-09-18 둘 다 바꿔놨어. 괜찮지?'),
    ])
    final = h.runtime.core.respond_to_text('모임 장소 바꿔줘').text
    assert '바꿔놨어' not in final and h.count_history() == 2
    assert h.observations()[-1]['calls'] == 2


def test_pending_target_revision_is_not_silently_refreshed(h):
    from companion.schedules import ScheduleCommand

    for date in ('내일', '금요일'):
        h.turn('모임 저장해줘', 'create', {'title': '모임', 'date': date})
    h.turn('모임 장소 바꿔줘', 'update', {'place': '카페'}, {'title_hint': '모임'})
    target = h.repo.query()[0]
    h.dialogue.service.execute(ScheduleCommand(
        'update', 'concurrent-writer', 1, '변경', {'time': '15:00'},
        {'schedule_id': target.id}, 1), source_text='변경')
    _, record = h.turn('그 모임 말이야', 'update', {'place': '카페'}, {'schedule_id': target.id})
    assert record['receipt']['reason_code'] == 'revision_conflict'
    assert h.repo.get(target.id).place is None and h.count_history() == 3


def test_existing_review_uses_remaining_budget_and_cannot_execute(h):
    from companion.grounding import ClaimViolation, ResponseClaimGate

    class Gate(ResponseClaimGate):
        def violations(self, text, evidence):
            return (ClaimViolation('capability', text),) if text == '내가 커피 마셨어.' else ()

    h.runtime.core._response_claim_gate = Gate()
    h.model.queue.extend([output('내가 커피 마셨어.'), output('안녕!')])
    assert h.runtime.core.respond_to_text('안녕').text == '안녕!'
    assert h.observations()[-1]['calls'] == 2
    # Parse retry consumes the repair allowance. The existing gate still blocks.
    h.model.queue.extend(['bad', output('내가 커피 마셨어.')])
    assert h.runtime.core.respond_to_text('안녕').text != '내가 커피 마셨어.'
    assert h.observations()[-1]['calls'] == 2 and h.count_history() == 0


def test_default_cli_config_activates_knowledge_path(monkeypatch, tmp_path):
    from companion import user_cli
    from companion.cli.__main__ import text_runtime_config

    monkeypatch.setattr(user_cli, 'DATA', tmp_path)
    parsed = user_cli.build_parser().parse_args(["--dialogue-policy", "companion"])
    args = user_cli.configure_persistent_args(parsed)
    config = text_runtime_config(args)
    assert config.dialogue_policy == 'companion'
    assert config.knowledge_db == tmp_path / 'knowledge.sqlite'


def test_rejected_proposal_does_not_poison_next_history(h):
    h.turn('일정 추가해줘', 'create', {'title': '전시', 'date': '내일'}, cue='없는 말',
           draft='바꿔놨어. 민수도 같이 가기로 했어.')
    h.turn('안녕', draft='안녕!')
    actual = h.model.requests[-1].messages
    assert not any('민수도 같이 가기로 했어' in m.content for m in actual)


def test_source_clock_is_fixed_before_generation(h):
    generate = h.model.generate

    def cross_midnight(request):
        h.clock.value = datetime(2026, 9, 15, 9, tzinfo=KST)
        return generate(request)
    h.model.generate = cross_midnight
    h.turn('내일 전시 저장해줘', 'create', {'title': '전시', 'date': '내일'})
    assert h.repo.query()[0].date == '2026-09-15' and h.clock.calls == 1


def test_clarification_missing_date_asks_with_receipt_without_write(h):
    h.model.queue.extend([
        output('저장했어', 'create', {'title': '전시'}, cue='전시'),
        output('전시는 어느 날짜로 남길까?'),
    ])
    final = h.runtime.core.respond_to_text('전시 일정 저장해줘').text
    assert final == '전시는 어느 날짜로 남길까?'
    assert h.observations()[-1]['receipt']['status'] == 'needs_clarification'
    assert h.observations()[-1]['calls'] == 2 and h.count_history() == 0


def test_followup_query_conflicting_time_is_not_displayed(h):
    h.turn('내일 전시 일정에 넣어줘', 'create',
           {'title': '전시', 'date': '내일', 'time': '15:00', 'place': '갤러리',
            'participants': '친구'})
    h.model.queue.extend([
        output('찾아볼게', 'query', target={'title_hint': '전시'}, cue='전시'),
        output('전시는 2026-09-15, 15:00, 갤러리에서 친구랑이야. 실제로는 16:00에 가.'),
    ])
    final = h.runtime.core.respond_to_text('전시 언제야?').text
    assert '16:00' not in final and '15:00' in final and h.count_history() == 1


def test_cancelled_schedule_in_variable_block_has_explicit_status(h):
    h.turn('내일 전시 저장해줘', 'create', {'title': '전시', 'date': '내일'})
    h.turn('전시 취소해줘', 'cancel', target={'title_hint': '전시'})
    h.turn('안녕', draft='안녕!')
    block = next(m for m in h.model.requests[-1].messages if m.content.startswith('확인된 일정'))
    assert json.loads(block.content.split('\n', 1)[1])['schedules'][0]['status'] == 'cancelled'


def test_model_exception_is_observed_without_write(h):
    h.model.queue.append(RuntimeError('model unavailable'))
    with pytest.raises(RuntimeError, match='model unavailable'):
        h.runtime.core.respond_to_text('안녕')
    record = h.observations()[-1]
    assert record['calls'] == 1 and record['error'] and record['receipt'] is None
    assert h.count_history() == 0


def test_supervised_json_error_keeps_session_open(h, monkeypatch):
    from io import StringIO

    from companion import text_child

    monkeypatch.setattr(text_child, 'build_text_runtime', lambda *a, **kw: h.runtime)
    h.model.queue.extend(['bad', 'bad', output('안녕!')])
    incoming = StringIO('\n'.join(json.dumps(frame) for frame in (
        {'kind': 'turn', 'id': '1', 'text': '일정 추가해줘'},
        {'kind': 'turn', 'id': '2', 'text': '안녕'}, {'kind': 'close'},
    )) + '\n')
    outgoing = StringIO()
    assert text_child.run(['--dialogue-policy', 'companion'], incoming, outgoing) == 0
    frames = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert [frame['kind'] for frame in frames] == ['ready', 'error', 'reply']
    assert '답변을 완성하지 못했어' in frames[1]['text']
    assert frames[2]['text'] == '안녕!' and h.count_history() == 0
    assert [record['calls'] for record in h.observations()] == [2, 1]


def test_context_date_window_includes_both_boundaries(h):
    h.clock.value = datetime(2026, 8, 1, 9, tzinfo=KST)
    for day in ('2026-08-30', '2026-08-31', '2026-09-28', '2026-09-29'):
        h.turn(f'{day} 전시 저장해줘', 'create', {'title': day, 'date': day})
    h.clock.value = datetime(2026, 9, 14, 9, tzinfo=KST)
    assert {s.date for s in h.dialogue.begin(99).context.schedules} == {
        '2026-08-31', '2026-09-28',
    }


@pytest.mark.parametrize('participants,expected', [(['민수', '지수'], '민수, 지수'),
                                                  ([], ''), ('민수', '민수')])
def test_participants_normalized_before_service(h, participants, expected):
    _, record = h.turn('내일 민수랑 지수 독서 모임 저장해줘', 'create',
                       {'title': '독서 모임', 'date': '내일', 'participants': participants})
    assert record['proposal']['fields']['participants'] == expected
    assert h.repo.query()[0].participants_text == expected


def test_cancel_fields_and_unknown_keys_are_observation_only(h):
    h.turn('내일 전시 저장해줘', 'create', {'title': '전시', 'date': '내일'})
    raw = json.loads(output('전시 취소했어.', 'cancel', {'time': ['invalid but ignored'],
                           'status': 'planned'}, {'title_hint': '전시', 'id': 'ignored'}))
    raw['extra'] = 'ignored'
    raw['schedule_proposal']['command_id'] = 'model-command'
    del raw['schedule_proposal']['cue']
    h.model.queue.append(json.dumps(raw))
    assert h.runtime.core.respond_to_text('전시 취소해줘').text == '전시 취소했어.'
    record = h.observations()[-1]
    assert record['receipt']['status'] == 'applied' and record['calls'] == 1
    assert record['proposal']['fields'] == {} and h.repo.query()[0].status == 'cancelled'
    assert set(record['warnings']) == {'ignored_key:response.extra',
        'ignored_key:proposal.command_id', 'ignored_key:target.id', 'ignored_key:fields.status',
        'ignored_cancel_fields', 'cue_fallback'}


@pytest.mark.parametrize('cue', [None, '이전 턴에서 복사한 말'])
@pytest.mark.parametrize('fields', [
    {'title': '영화', 'date': '내일'},
    {'title': '독서 모임', 'date': '내일', 'place': '남쪽 카페'},
    {'title': '독서 모임', 'date': '내일', 'participants': ['준호']},
    {'title': '독서 영화 모임', 'date': '내일'},
])
def test_ungrounded_create_fields_reject_without_write(h, cue, fields):
    h.model.queue.append(output('저장했어.', 'create', fields, cue=cue))
    final = h.runtime.core.respond_to_text('내일 북쪽 카페에서 민수랑 독서 모임 저장해줘').text
    record = h.observations()[-1]
    assert record['receipt']['reason_code'] == 'ungrounded_fields'
    assert record['calls'] == 1 and h.count_history() == 0 and '저장했어' not in final


def test_missing_target_and_cue_default_with_grounded_create(h):
    raw = json.loads(output('독서 모임 남겼어.', 'create',
                           {'title': '민수랑 독서 모임', 'date': '내일', 'participants': ['민수']}))
    del raw['schedule_proposal']['target'], raw['schedule_proposal']['cue']
    h.model.queue.append(json.dumps(raw))
    h.runtime.core.respond_to_text('내일 민수랑 독서 모임 저장해줘')
    record = h.observations()[-1]
    assert record['receipt']['status'] == 'applied' and record['cue_mode'] == 'field_grounding'
    assert 'default_target' in record['warnings']


@pytest.mark.parametrize('operation', ['update', 'cancel', 'query'])
def test_missing_or_stale_cue_leaves_target_resolution_to_service(h, operation):
    h.turn('내일 전시 저장해줘', 'create', {'title': '전시', 'date': '내일'})
    h.model.queue.append(output('응.', operation, {'place': '강변 갤러리'}
                               if operation == 'update' else {}, {'title_hint': '전시'}, '옛 원문'))
    h.runtime.core.respond_to_text('전시 요청')
    record = h.observations()[-1]
    assert record['receipt']['status'] == 'applied' and record['cue_mode'] == 'service_target'
    assert record['calls'] == (2 if operation == 'query' else 1)


@pytest.mark.parametrize('bad,reason', [
    ('plain text', 'Expecting value'),
    ('{"schedule_proposal":null}', 'reply_draft: required nonblank string'),
    ('{"reply_draft":"응"}', 'schedule_proposal: required null or object'),
    (output('응', 'create', {'participants': [42]}),
     'fields.participants: expected string or array of strings'),
    (output('응', 'create', {'time': 123}), 'fields.time: expected string or null'),
])
def test_retry_explains_specific_failure_in_one_system_line(h, bad, reason):
    h.model.queue.extend([bad, output('안녕!')])
    assert h.runtime.core.respond_to_text('안녕').text == '안녕!'
    message = h.model.requests[-1].messages[-1]
    assert message.role == 'system' and reason in message.content and '\n' not in message.content
    assert reason in h.observations()[-1]['validation_errors'][0]


@pytest.mark.parametrize('draft', [
    '응, 남겨뒀어. 독서 모임 기대되겠다.',
    '민수랑 도서관에서 독서 모임이 3시에 있네.',
    '내일 오후 3시 시청 도서관에서 민수랑 독서 모임이야.',
])
def test_natural_applied_draft_need_not_list_receipt(h, draft):
    final, record = h.turn('내일 민수랑 시청 도서관 독서 모임 저장해줘', 'create',
                           {'title': '독서 모임', 'date': '내일', 'time': '15:00',
                            'place': '시청 도서관', 'participants': ['민수']}, draft=draft)
    assert final == draft and record['discard_reason'] is None and record['calls'] == 1


@pytest.mark.parametrize('draft', [
    '2026-09-16에 저장했어.', '9월 16일이야.', '모레 독서 모임이네.', '금요일에 보자.',
    '오후 4시야.', '16:00으로 저장했어.', '4시 30분이네.',
    '남쪽 카페에서 민수랑 독서 모임이야.', '구청 도서관에서 만나.',
    '준호랑 독서 모임이야.', '참여자는 준호야.', '시간은 아직 안 정했네.',
    '장소는 남쪽 카페야.',
])
def test_explicit_contradictory_applied_drafts_replaced(h, draft):
    final, record = h.turn('내일 민수랑 시청 도서관 독서 모임 저장해줘', 'create',
                           {'title': '독서 모임', 'date': '내일', 'time': '15:00',
                            'place': '시청 도서관', 'participants': ['민수']}, draft=draft)
    assert final != draft and record['discard_reason'] == 'contradictory_applied_draft'
    assert record['receipt']['status'] == 'applied' and record['calls'] == 1


@pytest.mark.parametrize(
    'draft', ['오후 3시에 보자.', '강변 갤러리에서 볼 전시네.', '민수랑 가는구나.'])
def test_unknown_fields_cannot_be_invented_in_draft(h, draft):
    final, record = h.turn('내일 전시 저장해줘', 'create', {'title': '전시', 'date': '내일'},
                           draft=draft)
    assert final != draft and record['discard_reason'] == 'contradictory_applied_draft'


@pytest.mark.parametrize('source,iso,status,reason', [
    ('내일 독서 모임 저장해줘', '2026-09-15', 'applied', None),
    ('금요일 독서 모임 저장해줘', '2026-09-18', 'applied', None),
    ('9월 20일 독서 모임 저장해줘', '2026-09-20', 'applied', None),
    ('2026-09-20 독서 모임 저장해줘', '2026-09-20', 'applied', None),
    ('금요일 독서 모임 저장해줘', '2026-09-16', 'needs_clarification', 'date_mismatch'),
    ('독서 모임 저장해줘', '2026-09-15', 'needs_clarification', 'ungrounded_date'),
    ('내일이나 모레 독서 모임 저장해줘', '2026-09-15', 'needs_clarification', 'date_mismatch'),
    ('이번 주말 독서 모임 저장해줘', '2026-09-19', 'needs_clarification', 'date_mismatch'),
])
@pytest.mark.parametrize('exact_cue', [True, False])
def test_model_iso_compared_to_current_source(h, source, iso, status, reason, exact_cue):
    final, record = h.turn(source, 'create', {'title': '독서 모임', 'date': iso},
                           cue=source if exact_cue else '이전 발화', draft='저장했어.')
    assert (record['receipt']['status'], record['receipt']['reason_code']) == (status, reason)
    assert record['calls'] == (1 if status == 'applied' else 2)
    assert h.count_history() == (1 if status == 'applied' else 0)
    if status == 'applied':
        assert h.repo.query()[0].date == iso
    else:
        assert '어느 날짜' in final and '저장했어' not in final
        assert '날짜만 질문해' in h.model.requests[-1].messages[-1].content


@pytest.mark.parametrize('expression,iso', [('내일', '2026-09-15'), ('금요일', '2026-09-18'),
                                            ('9월 20일', '2026-09-20')])
def test_date_expression_is_preserved_until_python_normalization(h, expression, iso):
    _, record = h.turn(f'{expression} 독서 모임 저장해줘', 'create',
                       {'title': '독서 모임', 'date': expression})
    assert record['proposal']['fields']['date'] == expression
    assert record['receipt']['status'] == 'applied' and h.repo.query()[0].date == iso
    history = json.loads(h.repo.history(h.repo.query()[0].id)[0]['change_json'])
    assert history['command']['request']['fields']['date'] == expression
    assert history['command']['normalized_fields']['date'] == iso


def test_partial_title_revision_lookup_and_date_correction_followup(h):
    h.turn('내일 민수랑 도서관 독서 모임 저장해줘', 'create',
           {'title': '민수랑 도서관 독서 모임', 'date': '내일'})
    # A second revision proves lookup uses candidate matching, not the old substring path.
    h.turn('독서 모임 장소를 시청 도서관으로 바꿔줘', 'update', {'place': '시청 도서관'},
           {'title_hint': '민수랑 독서 모임'})
    original = h.repo.query()[0]
    assert original.revision == 2
    _, record = h.turn('독서 모임 날짜를 금요일로 바꿔줘', 'update', {'date': '2026-09-16'},
                       {'title_hint': '민수랑 독서 모임'}, draft='바꿔놨어.')
    assert record['receipt']['reason_code'] == 'date_mismatch'
    assert record['receipt']['schedule_id'] == original.id and h.repo.query()[0] == original
    final, record = h.turn('금요일이야', 'update', {'date': '금요일'},
                           {'schedule_id': original.id}, draft='금요일로 바꿔놨어.')
    assert record['receipt']['status'] == 'applied' and h.count_history() == 3
    assert h.repo.query()[0].date == '2026-09-18' and final == '금요일로 바꿔놨어.'


def test_date_schema_and_system_require_user_expression():
    from companion.dialogue.schedule_dialogue import INSTRUCTION, OUTPUT_SCHEMA

    fields = OUTPUT_SCHEMA['properties']['schedule_proposal']['anyOf'][1]['properties']['fields']
    description = fields['properties']['date']['description']
    for text in (INSTRUCTION, description):
        assert '사용자가 말한 표현을 그대로 적어라' in text
        assert all(example in text for example in ('내일', '금요일', '9월 20일'))
