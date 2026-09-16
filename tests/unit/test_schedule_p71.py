"""P7.1: omitted queries and unsupported schedule assertions, using synthetic stores."""

from dataclasses import replace
import hashlib
import json

import pytest
from test_schedule_dialogue import Harness, output

from companion.capabilities import OPERATION_DESCRIPTION
from companion.dialogue.schedule_dialogue import (
    ContextBundle,
    claims_ungrounded_schedule,
    receipt_text,
)
from companion.schedules import ChangeReceipt


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.runtime.close()


def seed(h):
    h.turn('내일 오후 2시 개발 회의 저장해줘', 'create',
           {'title': '개발 회의', 'date': '내일', 'time': '오후 2시'})
    h.turn('다음 주 금요일 오전 10시 치과 예약 저장해줘', 'create',
           {'title': '치과 예약', 'date': '다음 주 금요일', 'time': '오전 10시'})
    return h.repo.query()


@pytest.mark.parametrize('text', [
    '겨울아 내 일정 중에 가장 가까운 게 뭐야?', '내일 약속 있어?',
    '오늘 회의 없어?', '예약 언제야?', '어떤 알람이 있어?',
])
def test_missing_proposal_queries_empty_store_and_discards_raw_draft(h, text):
    hallucination = '내일 오후 2시에 회사에서 진행되는 E2E 모델 리뷰 미팅이 가장 가까워.'
    final, record = h.turn(text, draft=hallucination)
    assert final == '해당하는 일정은 없어.'
    assert record['auto_query'] is True
    assert record['discard_reason'] == 'auto_query_replaced_draft'
    assert record['calls'] == 2
    assert record['proposal'] == dict(operation='query', target={}, fields={}, cue='')
    assert record['receipt']['schedules'] == []
    assert json.loads(record['raw'][0])['reply_draft'] == hallucination
    assert h.count_history() == 0


def test_auto_query_reads_all_rows_without_inventing_date_filters(h):
    rows = seed(h)
    before = h.count_history()
    final, record = h.turn('내일 약속 있어?', draft='내일 오후 5시 영화 미팅이 있어.')
    assert len(rows) == len(record['receipt']['schedules']) == 2
    assert all(row.title in final and row.date in final for row in rows)
    assert '영화' not in final
    assert record['calls'] == 2 and h.count_history() == before
    assert h.repo.query() == rows


def test_auto_query_accepts_grounded_natural_followup(h):
    seed(h)
    h.model.queue.extend([
        output('내일 오후 5시 영화 미팅이 있어.'),
        output('가장 가까운 건 내일 오후 2시 개발 회의야.'),
    ])
    response = h.runtime.core.respond_to_text('가장 가까운 일정이 뭐야?')
    record = h.observations()[-1]
    assert response.text == '가장 가까운 건 내일 오후 2시 개발 회의야.'
    assert record['calls'] == 2 and record['auto_query']
    assert record['discard_reason'] == 'auto_query_replaced_draft'


@pytest.mark.parametrize('first', ['not json', 'transport'])
def test_auto_query_with_exhausted_budget_uses_receipt(h, first):
    from companion.errors import AdapterUnavailableError

    h.model.queue.append(first if first != 'transport' else AdapterUnavailableError(
        'incomplete reply'))
    final, record = h.turn('내 일정 중 가장 가까운 게 뭐야?', draft='내일 2시 회의가 있어.')
    assert final == '해당하는 일정은 없어.'
    assert record['calls'] == 2 and len(record['drafts']) == 1
    assert record['discard_reason'] == 'auto_query_replaced_draft'
    assert record['followup_discard_reason'] == 'call_budget_exhausted'


@pytest.mark.parametrize('followup', ['bad json', output('내일 2시 회의가 있어.'),
                                     output('응', 'query')])
def test_auto_query_bad_followup_falls_back_with_no_third_call(h, followup):
    h.model.queue.extend([output('내일 오후 2시 회사 미팅이 있어.'), followup])
    response = h.runtime.core.respond_to_text('내 일정 중 가장 가까운 게 뭐야?')
    record = h.observations()[-1]
    assert response.text == '해당하는 일정은 없어.'
    assert record['calls'] == 2 and record['auto_query']
    assert record['discard_reason'] == 'auto_query_replaced_draft'


@pytest.mark.parametrize('text', ['내일 뭐 먹지?', '오늘 기분이 어때?', '회의 얘기 좀 하자',
                                  '코덱스 쓸 수 있어?'])
def test_no_auto_query_for_ordinary_chat(h, text):
    final, record = h.turn(text, draft='천천히 얘기해보자.')
    assert final == '천천히 얘기해보자.'
    assert not record['auto_query'] and record['proposal'] is None
    assert record['receipt'] is None and record['calls'] == 1


def test_explicit_proposal_keeps_existing_query_filter(h):
    seed(h)
    _, record = h.turn('내일 약속 있어?', 'query')
    assert not record['auto_query']
    assert len(record['receipt']['schedules']) == 1
    assert record['receipt']['schedules'][0]['title'] == '개발 회의'


def test_unproposed_concrete_schedule_is_blocked(h):
    draft = '내일 오후 2시 회의가 있어.'
    final, record = h.turn('안녕', draft=draft)
    assert final == '저장된 일정에는 그런 게 없어. 내가 아는 일정만 말할게.'
    assert record['discard_reason'] == 'ungrounded_schedule_claim'
    assert json.loads(record['raw'][0])['reply_draft'] == draft
    assert not record['auto_query'] and record['receipt'] is None
    assert record['calls'] == 1


def test_matching_title_and_date_survive_without_receipt(h):
    seed(h)
    draft = '내일 오후 2시 개발 회의가 있어.'
    final, record = h.turn('안녕', draft=draft)
    assert final == draft and record['discard_reason'] is None
    assert record['calls'] == 1


@pytest.mark.parametrize('draft', [
    '모레 오후 2시 개발 회의가 있어.',  # Matching title, wrong date.
    '내일 오후 2시 치과 예약이 있어.',  # Title and date must match the SAME row.
    '내일 오후 2시 영화 미팅이 있어.',  # Matching date, no title overlap.
    '내일 오후 3시 개발 회의가 있어.',
])
def test_unsupported_claim_includes_actual_context_list(h, draft):
    rows = seed(h)
    final, record = h.turn('안녕', draft=draft)
    assert record['discard_reason'] == 'ungrounded_schedule_claim'
    expected = receipt_text(ChangeReceipt('applied', 'query', 'context', 1, schedules=rows))
    assert final == '저장된 일정에는 그런 게 없어. 내가 아는 일정만 말할게. ' + expected
    assert h.repo.query() == rows


@pytest.mark.parametrize('draft', [
    '내일 오후 2시 회의가 있다고 했을 때 말이야.',
    '내일 오후 2시 회의가 있다면 미리 준비하자.',
    '"내일 오후 2시 회의가 있어"라는 문장을 봤어.',
    '내일 오후 2시 회의가 있어?',
    '내일 오후 2시 회의는 없어.',
    '내일은 한가해. 회의 얘기나 하자.',
])
def test_reported_conditional_questions_and_separate_clauses_are_not_claims(h, draft):
    assert not claims_ungrounded_schedule(draft, (), h.clock.now())
    final, record = h.turn('안녕', draft=draft)
    assert final == draft and record['discard_reason'] is None


def test_reported_clause_does_not_hide_separate_fabrication(h):
    draft = '내일 회의가 있다고 했지. 모레 오후 2시 예약이 있어.'
    assert claims_ungrounded_schedule(draft, (), h.clock.now())


def test_non_schedule_question_does_not_correct_a_fabricated_schedule_into_evidence(h):
    seed(h)
    _, record = h.turn('오늘 어떻게 보내?', draft='모레 오후 2시 개발 회의가 있어.')
    assert not record['auto_query']
    assert record['discard_reason'] == 'ungrounded_schedule_claim'


@pytest.mark.parametrize('word', ['이번 주', '이번주', '다음 주', '다음주', '주말', '이번 주말'])
def test_week_claim_needs_a_matching_date_in_the_same_row(h, word):
    rows = seed(h)
    draft = f'{word} 개발 회의가 있어.'
    assert claims_ungrounded_schedule(draft, rows, h.clock.now()) == (
        word not in {'이번 주', '이번주'})


def test_grounded_weekend_claim_survives(h):
    rows = tuple(replace(row, date='2026-09-19') for row in seed(h))
    assert not claims_ungrounded_schedule('이번 주말 개발 회의가 있어.', rows, h.clock.now())
    assert not claims_ungrounded_schedule('주말 개발 회의가 있어.', rows, h.clock.now())


def test_time_only_claim_requires_matching_time_and_title(h):
    rows = seed(h)
    turn = h.dialogue.begin(99)
    turn.context = ContextBundle((replace(rows[0], time='14:00'),))
    assert h.dialogue.display(turn, '오후 2시 개발 회의가 있어.') == '오후 2시 개발 회의가 있어.'
    assert h.dialogue.display(turn, '오후 3시 개발 회의가 있어.').startswith('저장된 일정에는')


def test_self_model_wording_hash():
    assert hashlib.sha256(OPERATION_DESCRIPTION.encode()).hexdigest() == (
        '3733d6a3381071f3df6c5ed8a93fced586fa023a907661d4659f5c9784c93b0d')
