"""P2.3 bounded dialogue regression tests, with synthetic stores and model replies."""
from dataclasses import replace
from datetime import datetime

import pytest
from test_schedule_dialogue import Harness, output

from companion.contracts import ChatRequest, ConversationMessage
from companion.dialogue.schedule_dialogue import (
    INSTRUCTION,
    OUTPUT_SCHEMA,
    ContextBundle,
)
from companion.errors import AdapterUnavailableError
from companion.schedules import ChangeReceipt, source_query_dates, source_time


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.runtime.close()


def seed(h):
    h.turn('내일 민수랑 시청 도서관 모임 저장해줘', 'create',
           {'title': '모임', 'date': '내일', 'time': '15:00',
            'place': '시청 도서관', 'participants': '민수'})
    return h.repo.query()[0]


@pytest.mark.parametrize('expression,start,end', [
    ('오늘', '2026-09-14', '2026-09-14'), ('내일', '2026-09-15', '2026-09-15'),
    ('이번 주', '2026-09-14', '2026-09-20'), ('다음 주', '2026-09-21', '2026-09-27'),
    ('이번 주말', '2026-09-19', '2026-09-20'), ('금요일', '2026-09-18', '2026-09-18'),
    ('월요일', '2026-09-14', '2026-09-14'), ('9월 20일', '2026-09-20', '2026-09-20'),
    ('모임', '2026-08-31', '2026-09-28'),
])
def test_query_dates_from_current_kst_source(expression, start, end):
    now = datetime.fromisoformat('2026-09-13T15:00:00+00:00')
    assert source_query_dates(expression + ' 일정 뭐야?', now) == {
        'date_from': start, 'date_to': end,
    }


@pytest.mark.parametrize('with_dates', [True, False])
def test_query_ignores_model_bounds_and_preserves_natural_draft(h, with_dates):
    seed(h)
    draft = '내일 오후 3시에 도서관에서 민수랑 모임이야.'
    fields = {'date_from': '2099-01-01', 'date_to': 'broken'} if with_dates else {}
    final, record = h.turn('내일 모임 언제 어디서 누구랑 하기로 했지?', 'query', fields,
                           {'title_hint': '모임'}, draft=draft)
    assert final == draft and record['calls'] == 1 and h.count_history() == 1
    assert len(record['receipt']['schedules']) == 1


def test_query_without_expression_has_bounded_title_search(h):
    seed(h)
    h.turn('10월 20일 모임 저장해줘', 'create', {'title': '모임', 'date': '10월 20일'})
    _, record = h.turn('모임 언제야?', 'query', target={'title_hint': '모임'},
                       draft='내일 오후 3시에 도서관에서 민수랑 모임이야.')
    assert len(record['receipt']['schedules']) == 1 and h.count_history() == 2


def test_zero_query_can_preserve_confirmed_context_draft(h, monkeypatch):
    seed(h)
    original = h.dialogue.service.execute

    def empty(command, **kwargs):
        return replace(original(command, **kwargs), schedules=())

    monkeypatch.setattr(h.dialogue.service, 'execute', empty)
    draft = '내일 오후 3시에 도서관에서 민수랑 모임이야.'
    final, record = h.turn('모임 언제야?', 'query', target={'title_hint': '모임'}, draft=draft)
    assert final == draft and record['receipt']['schedules'] == []
    assert record['calls'] == 1 and 'query_draft_preserved_from_context' in record['warnings']


def test_empty_context_does_not_support_invented_query_answer(h):
    h.model.queue.extend([
        output('내일 오후 3시에 도서관에서 민수랑 모임이야.', 'query',
               target={'title_hint': '모임'}), output('해당하는 일정은 없어.'),
    ])
    assert h.runtime.core.respond_to_text('모임 언제야?').text == '해당하는 일정은 없어.'
    assert h.count_history() == 0


@pytest.mark.parametrize('operation', ['create', 'update', 'cancel'])
def test_stale_cue_is_silent_and_never_calls_service(h, monkeypatch, operation):
    seed(h)
    before = h.repo.query()

    def forbidden(*args, **kwargs):
        pytest.fail('A stale cue must not reach the service')

    monkeypatch.setattr(h.dialogue.service, 'execute', forbidden)
    draft = '제목부터 해설이 필요하네ㅋㅋ'
    final, record = h.turn('전시 제목은 왜 어려울까ㅋㅋ', operation,
                           {'title': '전시', 'date': '내일'} if operation == 'create'
                           else {'place': '강변 갤러리'} if operation == 'update' else {},
                           {} if operation == 'create' else {'title_hint': '모임'},
                           draft=draft, cue='지난번 일정 바꿔줘')
    assert final == draft and record['receipt'] is None
    assert record['discard_reason'] is None and record['calls'] == 1
    assert h.repo.query() == before and h.count_history() == 1


def test_no_change_is_not_a_reply_instruction(h):
    seed(h)
    draft = '그 모임 기대되네.'
    final, record = h.turn('모임 장소는 시청 도서관으로 해줘', 'update',
                           {'place': '시청 도서관'}, {'title_hint': '모임'}, draft=draft)
    assert record['receipt']['reason_code'] == 'no_change'
    assert final == draft and record['calls'] == 1 and h.count_history() == 1


@pytest.mark.parametrize('period,hour,expected', [
    ('오전', 10, '10:00'), ('오후', 10, '22:00'), ('저녁', 8, '20:00'),
    ('밤', 10, '22:00'), ('새벽', 2, '02:00'), ('밤', 12, '00:00'),
])
def test_missing_time_period_comes_only_from_same_number(h, period, hour, expected):
    _, record = h.turn(f'내일 {period} {hour}시 모임 저장해줘', 'create',
                       {'title': '모임', 'date': '내일', 'time': f'{hour}시'})
    assert record['receipt']['status'] == 'applied' and h.repo.query()[0].time == expected
    assert record['calls'] == 1


@pytest.mark.parametrize('source', ['10시', '오후 9시', '오전 10시나 오후 10시', '오전 10시 30분'])
def test_time_ambiguity_without_unique_same_hour_and_minute(source):
    assert source_time('10시', source) is None


def test_time_ambiguity_still_asks_without_writing(h):
    _, record = h.turn('내일 10시에 모임 저장해줘', 'create',
                       {'title': '모임', 'date': '내일', 'time': '10시'})
    assert record['receipt']['reason_code'] == 'ambiguous_time' and h.count_history() == 0


@pytest.mark.parametrize('cue', ['', '과거 모임 저장해줘'])
def test_same_value_question_downgraded_before_stale_cue(h, cue):
    item = seed(h)
    h.model.queue.append(output('내일 오후 3시에 도서관에서 민수랑 모임이야.', 'update',
                                {'date': item.date, 'time': '오후 3시', 'place': item.place,
                                 'participants': ['민수']}, {'schedule_id': item.id}, cue))
    h.runtime.core.respond_to_text('모임 언제 어디서 누구랑 하기로 했지?')
    record = h.observations()[-1]
    assert record['receipt']['operation'] == 'query'
    assert 'question_update_downgraded_to_query' in record['warnings']
    assert h.repo.get(item.id) == item and h.count_history() == 1 and record['calls'] == 1


def test_question_with_actual_new_value_is_not_downgraded(h):
    seed(h)
    _, record = h.turn('모임 장소를 중앙 카페로 바꿔줄래?', 'update',
                       {'place': '중앙 카페'}, {'title_hint': '모임'}, draft='중앙 카페로 바꿨어.')
    assert record['receipt']['operation'] == 'update' and h.count_history() == 2


def test_contradiction_uses_receipt_generation_and_only_one_write(h):
    h.model.queue.extend([
        output('모레 오후 4시에 중앙 카페에서 준호랑 모임이야.', 'create',
               {'title': '모임', 'date': '내일', 'time': '15:00', 'place': '시청 도서관',
                'participants': '민수'}, cue='내일 모임 저장해줘'),
        output('내일 도서관에서 민수랑 보는 거네. 기대되겠다.'),
    ])
    final = h.runtime.core.respond_to_text('내일 모임 저장해줘').text
    assert final == '내일 도서관에서 민수랑 보는 거네. 기대되겠다.'
    assert h.count_history() == 1 and h.observations()[-1]['calls'] == 2
    assert len([m for m in h.model.requests[-1].messages
                if m.content.startswith('확인된 일정')]) == 1


def test_exhausted_retry_budget_uses_receipt_template(h):
    h.model.queue.extend(['bad', output('모레야.', 'create', {'title': '모임', 'date': '내일'},
                                       cue='내일 모임 저장해줘')])
    final = h.runtime.core.respond_to_text('내일 모임 저장해줘').text
    assert '2026-09-15' in final and '모레' not in final
    assert h.observations()[-1]['calls'] == 2 and h.count_history() == 1


def test_incomplete_sse_retries_identical_request_once(h):
    h.model.queue.extend([AdapterUnavailableError('incomplete/empty/missing-finish SSE reply'),
                          output('한 장보다 딴생각이 더 길어졌네ㅋㅋ')])
    assert h.runtime.core.respond_to_text('소설 한 장 읽고 딴생각해ㅋㅋ').text == (
        '한 장보다 딴생각이 더 길어졌네ㅋㅋ')
    assert h.model.requests[-1] == h.model.requests[-2]
    assert h.observations()[-1]['calls'] == 2 and h.count_history() == 0


def test_repeated_incomplete_sse_raises_without_third_call(h):
    h.model.queue.extend([AdapterUnavailableError('incomplete/empty/missing-finish SSE reply')
                          for _ in range(2)])
    with pytest.raises(AdapterUnavailableError):
        h.runtime.core.respond_to_text('안녕')
    assert len(h.model.requests) == 2 and h.count_history() == 0


def test_json_retry_consumes_sse_retry_allowance(h):
    h.model.queue.extend(['bad', AdapterUnavailableError('incomplete reply')])
    with pytest.raises(AdapterUnavailableError):
        h.runtime.core.respond_to_text('안녕')
    assert len(h.model.requests) == 2 and h.count_history() == 0


def test_transient_failure_after_commit_does_not_execute_again(h):
    h.model.queue.extend([
        output('모레야.', 'create', {'title': '모임', 'date': '내일'}, cue='내일 모임 저장해줘'),
        AdapterUnavailableError('incomplete/empty/missing-finish SSE reply'),
    ])
    with pytest.raises(AdapterUnavailableError):
        h.runtime.core.respond_to_text('내일 모임 저장해줘')
    assert h.observations()[-1]['calls'] == 2 and h.count_history() == 1


def test_followup_replaces_old_context_and_pending_and_keeps_request_contract(h):
    item = seed(h)
    turn = h.dialogue.begin(99)
    turn.receipt = ChangeReceipt('needs_clarification', 'update', 'c', 99, schedules=(item,),
                                 reason_code='ambiguous_date', clarification='어느 날짜로 할까?')
    request = ChatRequest('모임 언제야?', messages=(
        ConversationMessage('system', '정체성'), ContextBundle((item,)).message(),
        ConversationMessage('system', '아직 실행하지 않은 대기 일정 제안:\nold'),
        ConversationMessage('user', '모임 언제야?'),
    ), response_format={'type': 'json_schema'}, max_tokens=768)
    repaired = h.dialogue.followup_request(turn, request)
    assert repaired.response_format == request.response_format and repaired.max_tokens == 768
    assert repaired.messages[:2] == (request.messages[0], request.messages[-1])
    payload = '\n'.join(m.content for m in repaired.messages)
    assert payload.count(item.id) == 1 and 'old' not in payload and 'created_at' not in payload
    assert turn.context.schedules == (item,)  # Original evidence survives zero-result queries.


def test_schema_explains_questions_and_preserves_period():
    proposal = OUTPUT_SCHEMA['properties']['schedule_proposal']['anyOf'][1]['properties']
    assert '값 변경 요청만 update' in proposal['operation']['description']
    assert '오전/오후를 포함해 원문 그대로' in proposal['fields']['properties']['time']['description']
    assert 'query fields는 비워둬' in INSTRUCTION


def test_query_short_title_uses_token_matching_inside_14_days(h):
    h.turn('내일 민수랑 도서관 독서 모임 저장해줘', 'create',
           {'title': '민수랑 도서관 독서 모임', 'date': '내일', 'participants': '민수'})
    _, record = h.turn('독서 모임 언제야?', 'query', target={'title_hint': '민수랑 독서 모임'},
                       draft='내일 민수랑 독서 모임이야.')
    assert len(record['receipt']['schedules']) == 1 and h.count_history() == 1


def test_query_full_recital_does_not_hide_wrong_relative_day(h):
    seed(h)
    h.model.queue.extend([
        output('모임: 2026-09-15, 15:00, 시청 도서관, 민수. 모레 가면 돼.', 'query',
               target={'title_hint': '모임'}),
        output('내일 오후 3시에 도서관에서 민수랑 모임이야.'),
    ])
    final = h.runtime.core.respond_to_text('모임 언제야?').text
    assert final == '내일 오후 3시에 도서관에서 민수랑 모임이야.'
    assert h.observations()[-1]['calls'] == 2 and h.count_history() == 1


def test_model_dates_alone_do_not_supply_a_query_filter(h):
    seed(h)
    _, record = h.turn('그건 뭐야?', 'query',
                       {'date_from': '2026-09-15', 'date_to': '2026-09-15'})
    assert record['receipt']['reason_code'] == 'missing_query_filter'
    assert h.count_history() == 1
