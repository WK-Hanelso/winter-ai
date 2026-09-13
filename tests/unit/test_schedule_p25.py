"""P2.5 current-source selection and bounded natural display regressions."""
import pytest
from test_schedule_dialogue import Harness, output

from companion.dialogue.schedule_dialogue import claims_new_completion


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.runtime.close()


def seed(h):
    for name, day, place in [('지수', '내일', '북쪽 카페'), ('준호', '금요일', '남쪽 카페')]:
        h.turn(f'{day} {name}랑 {place}에서 작업 모임 저장해줘', 'create',
               {'title': f'{name}랑 작업 모임', 'date': day,
                'participants': name, 'place': place})
    return h.repo.query()


@pytest.mark.parametrize('operation', ['update', 'cancel'])
@pytest.mark.parametrize('selector', ['id', 'hint', 'both'])
def test_model_selector_cannot_resolve_source_tie(h, operation, selector):
    rows = seed(h)
    target = {'schedule_id': rows[0].id} if selector != 'hint' else {}
    if selector != 'id':
        target['title_hint'] = rows[0].title
    fields = {'place': '동쪽 카페'} if operation == 'update' else {}
    _, record = h.turn('작업 모임 장소를 동쪽 카페로 바꿔줘' if fields else '작업 모임 취소해줘',
                       operation, fields, target)
    assert record['receipt']['reason_code'] == 'ambiguous_target'
    assert len(record['receipt']['schedules']) == 2
    assert h.repo.query() == rows and h.count_history() == 2


@pytest.mark.parametrize(
    'source', ['준호랑 작업 모임', '금요일 작업 모임', '남쪽 카페에서 작업 모임'])
def test_current_words_resolve_without_model_id(h, source):
    seed(h)
    _, record = h.turn(source + ' 장소를 동쪽 카페로 바꿔줘', 'update',
                       {'place': '동쪽 카페'}, {'title_hint': '작업 모임'})
    assert record['receipt']['status'] == 'applied'
    assert next(r for r in h.repo.query() if r.participants_text == '준호').place == '동쪽 카페'
    assert next(r for r in h.repo.query() if r.participants_text == '지수').revision == 1


def test_inconsistent_id_cannot_override_unique_words(h):
    rows = seed(h)
    _, record = h.turn('준호랑 작업 모임 취소해줘', 'cancel', target={'schedule_id': rows[0].id})
    assert record['receipt']['status'] != 'applied' and h.repo.query() == rows


@pytest.mark.parametrize('echo', [False, True])
def test_pending_patch_survives_old_value_echo_and_no_id(h, echo):
    rows = seed(h)
    h.turn('작업 모임 장소를 동쪽 카페로 바꿔줘', 'update', {'place': '동쪽 카페'},
           {'schedule_id': rows[0].id})
    _, record = h.turn('금요일에 준호랑 하는 모임 말이야', 'update',
                       {'place': '남쪽 카페'} if echo else {}, {'title_hint': '모임'})
    assert record['receipt']['status'] == 'applied'
    assert next(r for r in h.repo.query() if r.participants_text == '준호').place == '동쪽 카페'
    assert h.repo.get(rows[0].id) == rows[0] and h.count_history() == 3


@pytest.mark.parametrize('field,value,reason', [
    ('date', '2026-09-18', 'ungrounded_date'),
    ('date', '금요일', 'ungrounded_date'),
    ('place', '동쪽 카페', 'ungrounded_fields'),
])
def test_ungrounded_proposal_does_not_hijack_chat(h, field, value, reason):
    rows = seed(h)
    draft = '한 장 읽고 상상은 세 장쯤 했네ㅋㅋ'
    final, record = h.turn('소설 읽다가 딴생각했어ㅋㅋ', 'update', {field: value},
                           {'schedule_id': rows[0].id}, draft=draft, cue='지난번 모임')
    assert final == draft and record['receipt'] is None and reason in record['warnings']
    assert record['calls'] == 1 and h.repo.query() == rows


@pytest.mark.parametrize('source', ['주말쯤', '이번 주말', '월요일', '내일이나 모레', '금요일쯤'])
def test_ambiguous_current_date_still_asks_naturally(h, source):
    h.model.queue.extend([
        output('저장했어.', 'create', {'title': '독서 모임', 'date': source},
               cue=f'{source} 독서 모임'),
        output('정확히 어느 날짜로 할까?'),
    ])
    final = h.runtime.core.respond_to_text(f'{source} 독서 모임 저장해줘').text
    record = h.observations()[-1]
    assert record['receipt']['status'] == 'needs_clarification' and h.count_history() == 0
    expected = '9월 14일(월) 말하는 거야?' if source == '월요일' else '정확히 어느 날짜로 할까?'
    assert final == expected and record['calls'] == 2


def test_candidate_question_does_not_need_iso_or_full_title(h):
    seed(h)
    question = '화요일 지수랑 하는 모임이야, 금요일 준호랑 하는 모임이야?'
    h.model.queue.extend([
        output('바꿔놨어.', 'update', {'place': '동쪽 카페'}, {'title_hint': '작업 모임'}),
        output(question),
    ])
    final = h.runtime.core.respond_to_text('작업 모임 장소를 동쪽 카페로 바꿔줘').text
    assert final == question and h.observations()[-1]['calls'] == 2 and h.count_history() == 2


@pytest.mark.parametrize('draft,blocked', [
    ('내일 미용실 예약은 취소됐어. 가야 할 일정은 없어.', False),
    ('이미 미용실 예약 취소했어.', False),
    ('내가 아까 미용실 예약 취소했어.', False),
    ('방금 미용실 예약을 취소했어.', True),
    ('미용실 예약은 취소됐어. 방금 독서 모임도 저장했어.', True),
    ('독서 모임은 취소됐어.', True),
    ('미용실 예약은 취소됐고 독서 모임도 저장했어.', True),
    ('미용실 예약은 변경됐어.', True),
])
def test_context_state_is_not_new_completion(h, draft, blocked):
    h.turn('내일 중앙 미용실 예약 저장해줘', 'create',
           {'title': '중앙 미용실 예약', 'date': '내일'})
    h.turn('미용실 예약 취소해줘', 'cancel', target={'title_hint': '미용실 예약'})
    assert claims_new_completion(draft, h.repo.query(), h.clock.now()) is blocked


def test_null_proposal_after_restart_corrects_relative_day(h):
    h.turn('내일 서쪽 공원 산책 저장해줘', 'create',
           {'title': '서쪽 공원 산책', 'date': '내일', 'time': '20:00', 'place': '서쪽 공원'})
    h.runtime.close()
    h.clock.value = h.clock.value.replace(day=15)
    h.restart()
    final, record = h.turn('오늘 산책 언제 어디서 해?', draft='내일 오후 8시 서쪽 공원에서 산책해.')
    assert final == '오늘 오후 8시 서쪽 공원에서 산책해.'
    assert record['calls'] == 1 and 'context_relative_date_corrected' in record['warnings']


def test_conflicting_query_gets_receipt_generation(h):
    h.turn('내일 독서 모임 저장해줘', 'create', {'title': '독서 모임', 'date': '내일'})
    h.model.queue.extend([
        output('금요일 독서 모임이야.', 'query', target={'title_hint': '독서 모임'}),
        output('화요일에 독서 모임이 있어.'),
    ])
    final = h.runtime.core.respond_to_text('독서 모임 언제야?').text
    assert final == '화요일에 독서 모임이 있어.' and h.observations()[-1]['calls'] == 2
    assert 'receipt' in h.model.requests[-1].messages[-1].content


def test_ambiguous_time_uses_second_natural_question(h):
    h.turn('내일 독서 모임 저장해줘', 'create', {'title': '독서 모임', 'date': '내일'})
    h.model.queue.extend([
        output('3시로 바꿨어.', 'update', {'time': '3시'}, {'title_hint': '독서 모임'}),
        output('독서 모임은 오전 3시야, 오후 3시야?'),
    ])
    final = h.runtime.core.respond_to_text('독서 모임 시간을 3시로 바꿔줘').text
    record = h.observations()[-1]
    assert record['receipt']['reason_code'] == 'ambiguous_time'
    assert final == '독서 모임은 오전 3시야, 오후 3시야?' and record['calls'] == 2
    assert h.count_history() == 1


@pytest.mark.parametrize('iso', ['2026-09-16', '2026-09-10', '2026-02-30'])
def test_current_unique_weekday_precedes_even_invalid_model_date(h, iso):
    _, record = h.turn('금요일 독서 모임 저장해줘', 'create',
                       {'title': '독서 모임', 'date': iso})
    assert record['receipt']['status'] == 'applied'
    assert h.repo.query()[0].date == '2026-09-18' and h.count_history() == 1


def test_ambiguous_source_weekend_with_model_iso_asks(h):
    _, record = h.turn('주말쯤 독서 모임 저장해줘', 'create',
                       {'title': '독서 모임', 'date': '2026-09-19'})
    assert record['receipt']['status'] == 'needs_clarification' and h.count_history() == 0


def test_source_tie_is_rechecked_in_write_transaction(h, monkeypatch):
    h.turn('내일 작업 모임 저장해줘', 'create', {'title': '작업 모임', 'date': '내일'})
    first = h.repo.query()[0]
    execute = h.dialogue.service.execute

    def concurrent_candidate(command, **kwargs):
        from companion.schedules import ScheduleCommand

        # A trusted independent writer inserts a second candidate after Core's lookup.
        execute(ScheduleCommand('create', 'concurrent', 99, '저장',
                {'title': '준호 작업 모임', 'date': '금요일'}, expected_revision=0),
                source_text='저장')
        return execute(command, **kwargs)

    monkeypatch.setattr(h.dialogue.service, 'execute', concurrent_candidate)
    _, record = h.turn('작업 모임 장소를 동쪽 카페로 바꿔줘', 'update',
                       {'place': '동쪽 카페'}, {'schedule_id': first.id})
    assert record['receipt']['reason_code'] == 'ambiguous_target'
    assert h.repo.get(first.id) == first and h.count_history() == 2


def test_natural_question_cannot_swap_candidate_weekdays(h):
    seed(h)
    h.model.queue.extend([
        output('바꿨어.', 'update', {'place': '동쪽 카페'}, {'title_hint': '작업 모임'}),
        output('금요일 지수랑 하는 모임이야, 화요일 준호랑 하는 모임이야?'),
    ])
    final = h.runtime.core.respond_to_text('작업 모임 장소를 동쪽 카페로 바꿔줘').text
    assert final != '금요일 지수랑 하는 모임이야, 화요일 준호랑 하는 모임이야?'
    assert h.observations()[-1]['discard_reason'] == 'contradictory_clarification_draft'
    assert h.count_history() == 2
