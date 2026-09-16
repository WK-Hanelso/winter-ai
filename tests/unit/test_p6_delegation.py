"""P6 regressions retained under P7 model-owned delegation."""

from types import SimpleNamespace

import pytest
from test_schedule_dialogue import Harness, output
from test_text_session import run_host

from companion.capabilities import SelfModelSnapshot

QUESTIONS = (
    '그러면 codex를 이용해서 조사한후 그걸 알려줄수는 있는거야?',
    '아니 내가 말한것에 대해 다시 말해볼래? codex에게 README.md랑 pyproject.toml이랑 '
    'src랑 tests를 읽기범위로 준 이유가 뭐야?',
    '난 너한태 물어본건데 왜 codex에게 할당해?',
    '그러면 다시 물어볼께 왜 README.md, pyproject.toml src tests를 다 넘기는거야?',
)


def test_approval_prompt_snapshot(monkeypatch):
    monkeypatch.setattr('companion.text_host.uuid4', lambda: SimpleNamespace(hex='turn-1'))
    _, _, output_stream = run_host()
    expected = (
        '[외부 Codex / 읽기 전용]\n'
        '보낼 요청: Codex로 README 확인해줘\n'
        '이유: 실제 파일 확인이 필요해.\n'
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
        '- 필요하면 로컬 검색 서버로 웹을 검색해 출처와 함께 답한다. 검색어는 화면에 표시된다.',
        '- 코드 확인은 천우가 시키거나 네가 정말 코드를 봐야 답할 수 있다고 판단할 때, '
        '코덱스에 읽기 작업을 제안할 수 있다. 천우가 터미널에서 승인하면 Codex가 '
        '고정 읽기 범위(README.md, pyproject.toml, src/, tests/)만 읽고 답을 돌려준다. '
        "'코덱스 쓸 수 있어?'에는 '시키거나 필요하면 제안해서 쓸 수 있다'고 답해.",
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


@pytest.mark.parametrize('text', [*QUESTIONS, 'codex 쓸 수 있어?',
                                 '파이썬 데코레이터가 뭐야?', '오늘 너무 지쳤어'])
def test_questions_and_chat_use_model_reply_without_worker_request(tmp_path, text):
    h = Harness(tmp_path)
    draft = '지금 이야기부터 이어가자.'
    h.model.queue.append(output(draft))
    response = h.runtime.core.respond_to_text(text)
    assert response.text == draft and response.worker_request is None
    assert len(h.model.requests) == 1
    assert h.runtime.close()
