"""P3-2 display streaming; fake models, synthetic text and temporary stores only."""

from dataclasses import replace
from io import StringIO
import json
from types import SimpleNamespace

import pytest
from test_repeated_reply import FRESH, REPEATED, seed
from test_schedule_dialogue import Harness, Model, output
from test_text_session import wire

from companion.adapters.fake import FakeChatModel
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.contracts import ChatRequest
from companion.dialogue.json_reply_stream import ReplyDraftExtractor
from companion.dialogue.schedule_dialogue import OUTPUT_SCHEMA, parse_output
from companion.errors import AdapterUnavailableError
from companion.grounding import ClaimViolation, ResponseClaimGate
from companion.session_wire import SessionProtocolError, string
from companion.text_child import run
from companion.text_host import session


@pytest.mark.parametrize('reply_first', [False, True])
@pytest.mark.parametrize('size', [1, 2, 3, 7, 31, 1000])
def test_extractor_escapes_depth_and_key_order(reply_first, size):
    draft = '한글 "인용" \\ 경로\n\t\r\b\f/ 😀 끝'
    proposal = {'fields': {'title': 'reply_draft', 'reply_draft': '숨김'},
                'other': [{'reply_draft': '숨김2'}, '"reply_draft": "가짜"']}
    pairs = [('schedule_proposal', proposal), ('reply_draft', draft)]
    raw = json.dumps(dict(reversed(pairs) if reply_first else pairs), ensure_ascii=True)
    extractor = ReplyDraftExtractor()
    deltas = [extractor.feed(raw[i:i + size]) for i in range(0, len(raw), size)]
    assert ''.join(deltas) == draft
    assert extractor.feed(', "reply_draft": "다시"}') == ''
    assert ''.join(deltas).encode('utf-8').decode() == draft


def test_extractor_emits_before_value_or_document_closes():
    extractor = ReplyDraftExtractor()
    assert extractor.feed('{"schedule_proposal":null,"reply_draft":"안') == '안'
    assert extractor.feed('녕\\') == '녕'
    assert extractor.feed('uD83D\\u') == ''
    assert extractor.feed('DE00\\') == '😀'
    assert extractor.feed('"!"') == '"!'
    assert extractor.feed('}') == ''


@pytest.mark.parametrize('bad', [r'\uZZZZ', r'\q', r'\uDE00', r'\uD83Dx'])
def test_invalid_escape_stops_display_without_interrupting_full_validation(bad):
    extractor = ReplyDraftExtractor()
    assert extractor.feed('{"reply_draft":"앞' + bad + '뒤"}') == '앞'


class StreamingModel(Model):
    def __init__(self):
        super().__init__()
        self.stream_calls = 0
        self.closed = 0
        self.before_next = None
        self.fail_after = None

    def generate(self, request):
        pytest.fail('Core must select the stream method')

    def generate_stream(self, request):
        self.stream_calls += 1
        raw = super().generate(request).text
        try:
            for char in raw:
                yield char
                if self.before_next:
                    self.before_next()
            if self.fail_after:
                error, self.fail_after = self.fail_after, None
                raise error
        finally:
            self.closed += 1


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    harness.model = StreamingModel()
    harness.runtime.core._chat_model = harness.model
    yield harness
    assert harness.runtime.close()


def turn(h, text='안녕'):
    partials, sentences = [], []
    response = h.runtime.core.respond_to_text_streaming(
        text, sentences.append, on_partial=partials.append,
    )
    assert h.runtime.resources.repository.list_messages()[-1].content == response.text
    assert h.observations()[-1]['final'] == response.text
    assert h.observations()[-1]['calls'] == h.model.stream_calls
    assert h.model.closed == h.model.stream_calls
    return response, ''.join(partials), sentences


@pytest.mark.parametrize('reply_first', [False, True])
def test_core_partial_is_live_and_timing_includes_preparation(h, monkeypatch, reply_first):
    draft = '안녕! "인용" \\ 경로\n😀'
    pairs = [('schedule_proposal', None), ('reply_draft', draft)]
    h.model.queue.append(json.dumps(dict(reversed(pairs) if reply_first else pairs)))
    partials, sentences = [], []
    ticks = iter([100.0, 104.5])
    monkeypatch.setattr('companion.core.perf_counter', lambda: next(ticks))

    def before_next():
        assert not sentences
        if len(partials) == len(draft):
            # The closing quote/document have not yet been consumed by Core.
            assert ''.join(partials) == draft
        assert not any(m.role == 'assistant'
                       for m in h.runtime.resources.repository.list_messages())

    h.model.before_next = before_next

    def partial(delta):
        assert h.model.closed == 0  # Delivery must precede stream exhaustion.
        partials.append(delta)

    response = h.runtime.core.respond_to_text_streaming(
        '안녕', sentences.append, on_partial=partial,
    )
    assert partials == list(draft)
    assert response.text == parse_output(h.observations()[-1]['raw'][0])[0] == draft
    assert response.streamed_text is None
    record = h.observations()[-1]
    assert record['first_partial_seconds'] == 4.5
    assert record['streamed_matches_final'] is True
    assert record['calls'] == h.model.stream_calls == 1
    assert sentences


@pytest.mark.parametrize('cause', ['display', 'receipt', 'claim', 'repeat', 'invalid_json'])
def test_final_correction_preserves_budget_receipt_and_storage(h, cause):
    draft, final = '처음 초안이야.', '안녕!'
    prompt = '안녕'
    if cause == 'display':
        draft = '일정 저장했어.'
        h.model.queue.append(output(draft))
    elif cause == 'receipt':
        draft = '금요일 독서 모임 저장했어.'
        prompt = '내일 독서 모임 일정 저장해줘'
        h.model.queue.append(output(draft, 'create', dict(title='독서 모임', date='내일')))
        # The inherited fake reads the real followup receipt for call 2.
    elif cause == 'claim':
        class Gate(ResponseClaimGate):
            def violations(self, text, evidence):
                return (ClaimViolation('capability', text),) if text == draft else ()

        h.runtime.core._response_claim_gate = Gate()
        h.model.queue.extend([output(draft), output(final)])
    elif cause == 'repeat':
        draft, final = REPEATED, FRESH
        seed(h.runtime.resources.repository, draft)
        h.model.queue.extend([output(draft), output(final)])
    else:
        h.model.queue.extend([output(draft)[:-1], output(final)])
    response, streamed, _ = turn(h, prompt)
    assert streamed == response.streamed_text == draft
    assert response.text != draft
    assert h.observations()[-1]['streamed_matches_final'] is False
    assert h.model.stream_calls == (1 if cause == 'display' else 2)
    assert h.count_history() == (1 if cause == 'receipt' else 0)
    assert all(r.response_format == h.model.requests[0].response_format for r in h.model.requests)


def test_disconnect_preserves_error_and_never_executes_even_complete_partial_json(h):
    h.model.queue.append(output('내일 독서 모임 저장했어.', 'create',
                                dict(title='독서 모임', date='내일')))
    h.model.fail_after = AdapterUnavailableError('local server disconnected')
    emitted = []
    with pytest.raises(AdapterUnavailableError, match='disconnected'):
        h.runtime.core.respond_to_text_streaming(
            '내일 독서 모임 저장해줘', lambda _: None, on_partial=emitted.append,
        )
    assert emitted and h.model.closed == h.model.stream_calls == 1
    assert not h.repo.query() and h.count_history() == 0
    assert h.runtime.resources.repository.list_messages()[-1].role == 'user'
    record = h.observations()[-1]
    assert record['final'] is None and record['error'].startswith('AdapterUnavailableError:')
    assert record['first_partial_seconds'] is not None
    assert record['streamed_matches_final'] is False


def test_existing_incomplete_retry_does_not_append_another_draft(h):
    h.model.queue.extend([output('처음 초안'), output('안녕!')])
    h.model.fail_after = AdapterUnavailableError('incomplete reply')
    response, streamed, _ = turn(h)
    assert streamed == response.streamed_text == '처음 초안'
    assert response.text == '안녕!' and h.model.stream_calls == 2


def test_no_stream_keeps_existing_generation_and_emits_no_partial(h):
    model = Model()
    model.queue.append(output('안녕!'))
    h.runtime.core._chat_model = model
    emitted = []
    response = h.runtime.core.respond_to_text_streaming(
        '안녕', lambda _: None, on_partial=emitted.append,
    )
    assert response.text == '안녕!' and response.streamed_text is None
    assert not emitted and len(model.requests) == 1
    record = h.observations()[-1]
    assert record['first_partial_seconds'] is None and record['streamed_matches_final'] is False


def test_fake_chat_model_without_stream_uses_generate(h):
    state = h.dialogue.begin(1)
    request = ChatRequest(prompt=output('안녕!'))
    emitted = []
    assert h.dialogue.generate(state, FakeChatModel(prefix=''), request,
                              on_partial=emitted.append) == '안녕!'
    assert state.calls == 1 and not emitted


def test_nonstream_entry_and_old_sentence_signature_stay_final_only(h):
    h.model.queue.append(output('안녕!'))
    assert h.runtime.core.respond_to_text('안녕').text == '안녕!'
    assert h.observations()[-1]['first_partial_seconds'] is None
    h.model.queue.append(output('또 왔네.'))
    sentences = []
    response = h.runtime.core.respond_to_text_streaming('다시 왔어', sentences.append, None)
    assert response.text == ''.join(sentences) == '또 왔네.'
    assert response.streamed_text is None


def test_schema_and_followup_keep_proposal_first(h):
    assert list(OUTPUT_SCHEMA['properties']) == [
        'schedule_proposal', 'codex_request', 'reply_draft',
    ]
    assert OUTPUT_SCHEMA['required'] == ['schedule_proposal', 'codex_request', 'reply_draft']


def test_codex_command_uses_normal_streaming_model_without_regex_route(h):
    h.runtime.core._read_only_worker = SimpleNamespace(
        execute=lambda request: pytest.fail('Core must not execute Worker'),
    )
    h.model.queue.append(output('실제 코드를 봐야 알 수 있어.'))
    emitted = []
    response = h.runtime.core.respond_to_text_streaming(
        'Codex로 README 확인해줘', lambda _: None, on_partial=emitted.append,
    )
    assert response.worker_request is None
    assert ''.join(emitted) == response.text and h.model.stream_calls == 1


def host(monkeypatch, frames):
    monkeypatch.setattr('companion.text_host.uuid4', lambda: SimpleNamespace(hex='t'))
    terminal = StringIO()
    session(StringIO(), wire({'kind': 'ready', 'name': '겨울이'}, *frames),
            StringIO('안녕\n/exit\n'), terminal,
            lambda _: pytest.fail('no Worker execution'), scope='fixture')
    return terminal.getvalue().split('You> ', 1)[1]


def frame(kind, text, turn_id='t'):
    return dict(kind=kind, id=turn_id, text=text)


@pytest.mark.parametrize('deltas,final,expected', [
    (['안', '녕', ' ', '😀'], '안녕 😀', '겨울이> 안녕 😀\nYou> '),
    ([], '안녕', '겨울이> 안녕\nYou> '),
    (['초', '안'], '최종', '겨울이> 초안\n겨울이> (정정) 최종\nYou> '),
    (['안', '\n', '녕'], '안\n녕', '겨울이> 안\n녕\nYou> '),
])
def test_host_rendering(monkeypatch, deltas, final, expected):
    assert host(monkeypatch, [*(frame('partial', d) for d in deltas),
                              frame('reply', final)]) == expected


@pytest.mark.parametrize('frames', [
    [frame('partial', '이전 턴', 'old')],
    [frame('partial', '')],
    [frame('partial', 'x' * 8193)],
    [frame('partial', 1)],
    [{**frame('partial', '초안'), 'extra': True}],
    [frame('partial', '초안'), dict(kind='worker_request', id='t', task='안녕')],
    [dict(kind='worker_request', id='t', task='안녕'), frame('partial', '초안')],
    [frame('partial', 'x' * 8192)] * 8,
])
def test_host_rejects_invalid_partial_protocol(monkeypatch, frames):
    with pytest.raises(SessionProtocolError):
        host(monkeypatch, frames)


def test_host_error_closes_partial_line(monkeypatch):
    assert host(monkeypatch, [frame('partial', '초안'), frame('error', '실패')]) == (
        '겨울이> 초안\n겨울이> 실패\nYou> ')


@pytest.mark.parametrize('failed', [False, True])
def test_real_child_sends_partials_then_final_and_stores_only_final(h, monkeypatch, failed):
    h.model.queue.append(output('안녕!'))
    if failed:
        h.model.fail_after = AdapterUnavailableError('disconnected')
    monkeypatch.setattr('companion.user_cli.DATA', h.path)
    monkeypatch.setattr('companion.text_child.build_text_runtime', lambda *a, **kw: h.runtime)
    out = StringIO()
    assert run(['--dialogue-policy', 'companion'], wire(
        frame('turn', '안녕'), {'kind': 'close'},
    ), out) == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [f['kind'] for f in frames] == ['ready', 'partial', 'partial', 'partial',
                                        'error' if failed else 'reply']
    assert ''.join(f['text'] for f in frames[1:-1]) == '안녕!'
    assert all(f['id'] == 't' for f in frames[1:])
    messages = SqliteConversationRepository(h.path / 'conversations.sqlite').list_messages()
    assert len(messages) == (1 if failed else 2)
    assert messages[-1].content == ('안녕' if failed else frames[-1]['text'])


def test_child_chunks_large_and_whitespace_deltas(h, monkeypatch):
    monkeypatch.setattr('companion.user_cli.DATA', h.path)
    monkeypatch.setattr('companion.text_child.build_text_runtime', lambda *a, **kw: h.runtime)
    h.model.queue.append(output('안녕!'))
    response = h.runtime.core.respond_to_text('안녕')

    def fake_stream(text, on_sentence, on_partial):
        on_partial(' ')
        on_partial('x' * 9000)
        return replace(response, text='최종')

    monkeypatch.setattr(h.runtime.core, 'respond_to_text_streaming', fake_stream)
    out = StringIO()
    run(['--dialogue-policy', 'companion'], wire(frame('turn', '안녕'), {'kind': 'close'}), out)
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [len(f['text']) for f in frames if f['kind'] == 'partial'] == [1, 8192, 808]
    with pytest.raises(SessionProtocolError):
        string(' ')
    assert string(' ', allow_whitespace=True) == ' '


def test_host_flushes_partial_before_reading_final(monkeypatch):
    terminal = StringIO()
    events = [{'kind': 'ready', 'name': '겨울이'}, frame('partial', '안'),
              frame('partial', '녕'), frame('reply', '안녕')]

    class ChildOutput(StringIO):
        reads = 0

        def readline(self, size=-1):
            if self.reads == 2:
                assert terminal.getvalue().endswith('겨울이> 안')
            if self.reads == 3:
                assert terminal.getvalue().endswith('겨울이> 안녕')
            self.reads += 1
            return super().readline(size)

    monkeypatch.setattr('companion.text_host.uuid4', lambda: SimpleNamespace(hex='t'))
    session(StringIO(), ChildOutput(wire(*events).getvalue()), StringIO('안녕\n/exit\n'),
            terminal, lambda _: pytest.fail('no Worker'), scope='fixture')


def test_partial_after_final_is_rejected_on_next_turn(monkeypatch):
    ids = iter(['t', 'next'])
    monkeypatch.setattr('companion.text_host.uuid4', lambda: SimpleNamespace(hex=next(ids)))
    with pytest.raises(SessionProtocolError, match='current turn'):
        session(StringIO(), wire({'kind': 'ready', 'name': '겨울이'}, frame('reply', '안녕'),
                                 frame('partial', '늦은 초안')),
                StringIO('안녕\n다시 왔어\n'), StringIO(),
                lambda _: pytest.fail('no Worker'), scope='fixture')
