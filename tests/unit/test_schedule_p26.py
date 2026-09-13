"""P2.6 calendar and visible clarification through Core; synthetic stores/models only."""

from datetime import datetime

import pytest
from test_schedule_dialogue import Harness, output

from companion.schedules import KST


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.runtime.close()


@pytest.mark.parametrize('today', [13, 14])
@pytest.mark.parametrize('model_date', ['토요일', '이번 주 토요일', '2026-09-12'])
def test_current_words_restore_this_week_and_create_next_saturday(h, today, model_date):
    h.clock.value = datetime(2026, 9, today, 9, tzinfo=KST)
    final, record = h.turn(
        '이번 주 토요일 오후 2시에 한강에서 동생이랑 자전거 일정 저장해줘',
        'create', dict(title='자전거', date=model_date, time='오후 2시',
                       place='한강', participants='동생'),
        draft='토요일에 자전거 타는 일정 저장했어.', streaming=True,
    )
    assert (record['receipt']['status'], record['calls']) == ('applied', 1)
    assert final == '토요일에 자전거 타는 일정 저장했어.'
    row, = h.repo.query()
    assert (row.date, row.time, row.place, row.participants_text) == (
        '2026-09-19', '14:00', '한강', '동생')
    assert h.count_history() == 1


@pytest.mark.parametrize('expression,reason,question', [
    ('9월 12일', 'past_date', '9월 12일(토) 말하는 거야?'),
    ('일요일', 'ambiguous_date', '9월 13일(일) 말하는 거야?'),
])
@pytest.mark.parametrize('model_question', ['그날 맞아?', '9월 19일(토) 말하는 거야?'])
def test_model_cannot_drop_or_change_calculated_clarification(h, expression, reason,
                                                            question, model_question):
    h.clock.value = datetime(2026, 9, 13, 9, tzinfo=KST)
    source = f'{expression} 자전거 일정 저장해줘'
    h.model.queue.extend([
        output('저장했어.', 'create', dict(title='자전거', date=expression), cue=source),
        output(model_question),
    ])
    final = h.runtime.core.respond_to_text(source).text
    record = h.observations()[-1]
    assert record['receipt']['reason_code'] == reason and record['calls'] == 2
    assert final == question
    assert h.repo.query() == () and h.count_history() == 0
