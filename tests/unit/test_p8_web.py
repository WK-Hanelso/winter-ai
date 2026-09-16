"""P8 contracts with fake HTTP/models and isolated Core stores, no live services."""

from email.message import Message
from io import BytesIO, StringIO
import json
from types import SimpleNamespace
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from configs.dialogue import web
import pytest
from test_reply_streaming import StreamingModel
from test_schedule_dialogue import Harness, output
from test_text_session import wire

from companion.adapters.web_search import PAGE_BYTES, SearXNGWebSearch
from companion.dialogue.json_reply_stream import ReplyDraftExtractor
from companion.dialogue.schedule_dialogue import (
    INSTRUCTION,
    OUTPUT_SCHEMA,
    WEB_FOLLOWUP,
    WEB_INSTRUCTION,
    parse_output,
)
from companion.session_wire import SessionProtocolError
from companion.text_child import run
from companion.text_host import session
from companion.web_search import (
    WebRequest,
    WebSearchError,
    WebSearchItem,
    WebSearchResult,
    render_results,
)

QUERY = '내일 서울 날씨'
WEB = dict(query=QUERY, why='예보를 확인해야 해.')
FINAL = '내일 서울에는 비가 와. (출처: weather.example)'
ITEM = WebSearchItem('서울 예보', 'https://weather.example/seoul', '비 예보', ('engine',),
                     '내일 서울에는 비가 와. 검색 블록 전용 표식.')


def proposed(web_request=WEB, draft='찾아볼게', **kwargs):
    payload = json.loads(output(draft, **kwargs))
    payload['web_request'] = web_request
    return json.dumps(payload, ensure_ascii=False)


class Search:
    def __init__(self, result=None, events=None):
        self.result = result if result is not None else WebSearchResult(QUERY, (ITEM,))
        self.queries = []
        self.events = events if events is not None else []

    def search(self, query):
        self.queries.append(query)
        self.events.append('search')
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    harness.model = StreamingModel()
    harness.runtime.core._chat_model = harness.model
    harness.dialogue.web_search = Search()
    yield harness
    harness.runtime.close()


def test_schema_fixed_head_and_bounds():
    keys = ['schedule_proposal', 'codex_request', 'web_request', 'reply_draft']
    assert list(OUTPUT_SCHEMA['properties']) == OUTPUT_SCHEMA['required'] == keys
    spec = OUTPUT_SCHEMA['properties']['web_request']['anyOf'][1]
    assert spec['required'] == ['query', 'why'] and spec['additionalProperties'] is False
    for key, limit in [('query', 200), ('why', 120)]:
        assert spec['properties'][key] == dict(type='string', minLength=1, maxLength=limit)
    assert 'pattern' not in json.dumps(OUTPUT_SCHEMA)
    assert INSTRUCTION.endswith(WEB_INSTRUCTION) and WEB_INSTRUCTION.count('천우:') == 6
    assert parse_output(proposed())[3] == WebRequest(**WEB)
    assert parse_output(proposed(None))[3] is None
    assert parse_output(proposed(dict(query='가'*200, why='나'*120)))[3]


@pytest.mark.parametrize('bad', [True, [], '', {}, {'query': QUERY}, dict(WEB, extra='x'),
    dict(WEB, query=None), dict(WEB, why=1), dict(WEB, query=''), dict(WEB, why=''),
    dict(WEB, query=' \n\t'), dict(WEB, why=' \n\t'),
    dict(WEB, query='가'*201), dict(WEB, why='나'*121)])
def test_parser_rejects_invalid_web(bad):
    with pytest.raises(ValueError, match='web_request'):
        parse_output(proposed(bad))


@pytest.mark.parametrize('size', [1, 2, 7, 1000])
@pytest.mark.parametrize('web_value', [WEB, None, 'missing', 'after'])
def test_only_explicit_null_before_reply_can_stream(size, web_value):
    payload = json.loads(proposed(None if web_value in ('missing', 'after') else web_value))
    if web_value in ('missing', 'after'):
        del payload['web_request']
    if web_value == 'after':
        payload['web_request'] = WEB
    raw = json.dumps(payload)
    extractor = ReplyDraftExtractor()
    streamed = ''.join(extractor.feed(raw[i:i+size]) for i in range(0, len(raw), size))
    assert streamed == ('찾아볼게' if web_value is None else '')


def test_same_turn_stream_status_evidence_storage_and_observations(h):
    h.model.queue.extend([proposed(), proposed(None, FINAL)])
    events, partials = [], []
    h.dialogue.web_search.events = events

    def partial(delta):
        assert h.model.stream_calls == 2  # First draft never reaches display.
        partials.append(delta)

    reply = h.runtime.core.respond_to_text_streaming(
        '내일 서울 날씨 어때?', lambda _: None, on_partial=partial,
        on_status=lambda text: events.append(text))
    assert events == ['검색: ' + QUERY, 'search']
    assert reply.text == ''.join(partials) == FINAL
    assert h.model.stream_calls == 2 and h.dialogue.web_search.queries == [QUERY]
    first, second = h.model.requests
    assert second.messages[:len(first.messages)] == first.messages
    assert second.messages[-2].content == WEB_FOLLOWUP
    assert second.messages[-1].content == render_results(WebSearchResult(QUERY, (ITEM,)))
    assert [m.content for m in h.runtime.resources.repository.list_messages()] == [
        '내일 서울 날씨 어때?', FINAL]
    record = h.observations()[-1]
    assert record['web_requested'] and record['web_query'] == QUERY
    assert record['web_results'] == record['web_pages'] == 1 and record['web_seconds'] >= 0
    assert record['streamed_matches_final'] and record['web_grounded']
    claims = [json.loads(line) for line in
              (h.path / 'claim_observations.jsonl').read_text().splitlines()]
    outside = [v for row in claims for v in row['violations'] if v['kind'] == 'outside_event']
    assert outside and all(v['web_grounded'] and v['action'] == 'observe' for v in outside)
    assert all('검색 블록 전용 표식' not in m.content
               for m in h.runtime.resources.repository.list_messages())


@pytest.mark.parametrize('failure', [WebSearchError('server absent'), TimeoutError('timeout'),
                                    WebSearchResult(QUERY)])
def test_search_failure_one_call_no_partial_fixed_answer(h, failure):
    h.dialogue.web_search = Search(failure)
    h.model.queue.append(proposed())
    partials = []
    reply = h.runtime.core.respond_to_text_streaming('내일 서울 날씨 검색해봐', lambda _: None,
                                                    on_partial=partials.append)
    expected = (f"찾아봤는데 '{QUERY}'로는 쓸 만한 결과가 없었어."
                if isinstance(failure, WebSearchResult) else '검색이 안 돼서 지금은 못 찾았어.')
    assert reply.text == expected and not partials and h.model.stream_calls == 1
    assert h.observations()[-1]['web_results'] == h.observations()[-1]['web_pages'] == 0
    assert h.runtime.resources.repository.list_messages()[-1].content == expected


def test_disabled_search_uses_fixed_failure(h):
    h.dialogue.web_search = None
    h.model.queue.append(proposed())
    assert h.runtime.core.respond_to_text('검색해봐').text == '검색이 안 돼서 지금은 못 찾았어.'
    assert h.model.stream_calls == 1


@pytest.mark.parametrize('second_web', [WEB, {'invalid': True}])
def test_second_search_request_ignored_and_answer_still_streams(h, second_web):
    h.model.queue.extend([proposed(), proposed(second_web, FINAL)])
    partials = []
    reply = h.runtime.core.respond_to_text_streaming('서울 날씨 검색해봐', lambda _: None,
                                                    on_partial=partials.append)
    assert reply.text == ''.join(partials) == FINAL
    assert h.model.stream_calls == 2 and len(h.dialogue.web_search.queries) == 1


def test_search_not_taken_from_retry_or_schedule_followup(h):
    h.model.queue.extend(['invalid json', proposed(WEB, '응.')])
    assert h.runtime.core.respond_to_text('안녕').text == '응.'
    assert not h.dialogue.web_search.queries and not h.observations()[-1]['web_requested']


def test_malformed_search_answer_never_gets_third_call(h):
    h.model.queue.extend([proposed(), 'invalid'])
    assert h.runtime.core.respond_to_text('검색해봐').text == '검색 결과로 답변을 완성하지 못했어.'
    assert h.model.stream_calls == 2


def test_search_with_schedule_receipt_keeps_two_call_budget(h):
    h.model.queue.extend([
        proposed(operation='create', fields=dict(title='독서 모임', date='내일'),
                 cue='내일 독서 모임 일정 저장해줘'),
        proposed(None, '독서 모임 일정 저장했어.'),
    ])
    reply = h.runtime.core.respond_to_text('내일 독서 모임 일정 저장해줘. 서울 날씨도 검색해봐')
    assert reply.text == '독서 모임 일정 저장했어.' and len(h.repo.query()) == 1
    assert h.model.stream_calls == 2
    assert any('receipt' in m.content for m in h.model.requests[-1].messages)


def test_child_status_precedes_second_partials_and_reply(h, monkeypatch):
    h.model.queue.extend([proposed(), proposed(None, FINAL)])
    monkeypatch.setattr('companion.user_cli.DATA', h.path)
    monkeypatch.setattr('companion.text_child.build_text_runtime', lambda *a, **kw: h.runtime)
    out = StringIO()
    assert run(['--dialogue-policy', 'companion'], wire(
        dict(kind='turn', id='one', text='내일 서울 날씨 어때?'), dict(kind='close')), out) == 0
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert frames[1] == dict(kind='status', id='one', text='검색: ' + QUERY)
    assert all(f['kind'] == 'partial' for f in frames[2:-1])
    assert ''.join(f['text'] for f in frames[2:-1]) == FINAL
    assert frames[-1] == dict(kind='reply', id='one', text=FINAL)


def host(monkeypatch, frames):
    monkeypatch.setattr('companion.text_host.uuid4', lambda: SimpleNamespace(hex='turn'))
    out = StringIO()
    session(StringIO(), wire(dict(kind='ready', name='겨울이'), *frames),
            StringIO('검색해봐\n/exit\n'), out, lambda _: pytest.fail('no Worker'), scope='fixture')
    return out.getvalue()


def test_host_status_one_line_then_answer(monkeypatch):
    display = host(monkeypatch, [dict(kind='status', id='turn', text='검색: '+QUERY),
        dict(kind='reply', id='turn', text=FINAL)])
    assert display.index('[검색] ' + QUERY + '\n') < display.index('겨울이> '+FINAL)
    assert '승인' not in display


@pytest.mark.parametrize('bad', [dict(kind='status', id='wrong', text='검색: x'),
    dict(kind='status', id='turn', text='검색: '), dict(kind='status', id='turn', text='x'),
    dict(kind='status', id='turn', text='검색: x', extra=True)])
def test_host_invalid_status_rejected(monkeypatch, bad):
    with pytest.raises(SessionProtocolError):
        host(monkeypatch, [bad])


def test_host_status_after_reply_rejected(monkeypatch):
    with pytest.raises(SessionProtocolError, match='status after'):
        host(monkeypatch, [dict(kind='reply', id='turn', text=FINAL, worker_pending=True),
                          dict(kind='status', id='turn', text='검색: x')])


class HttpResponse(BytesIO):
    def __init__(self, data, mime='text/html; charset=utf-8'):
        super().__init__(data)
        self.headers = Message()
        self.headers['Content-Type'] = mime
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def row(index):
    return dict(title=f'제목 {index}', url=f'https://site{index}.example/page',
                content=f'요약 {index}', engines=['one', 'two'])


def test_adapter_query_top_n_pages_mime_limits_and_fallback(monkeypatch):
    calls, pages = [], []

    def http(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        calls.append((url, timeout))
        if '/search?' in url:
            return HttpResponse(json.dumps(dict(results=[row(i) for i in range(9)],
                        unresponsive_engines=[['bing', 'timeout']])).encode(), 'application/json')
        if 'site0' in url:
            response = HttpResponse(('<html><script>숨길 스크립트</script>'
                '<style>숨길 스타일</style><nav><div>숨길 메뉴</div></nav>'
                '<main>본문 &amp; 내용<br>다음 문장 ' + '가'*400_000 + '</main>').encode())
            pages.append(response)
            return response
        if 'site1' in url:
            return HttpResponse(b'not HTML', 'application/pdf')
        raise URLError('page unavailable')

    monkeypatch.setattr('companion.adapters.web_search.urlopen', http)
    result = SearXNGWebSearch().search(QUERY)
    assert len(result.results) == web.MAX_RESULTS == 5 and result.pages == 1
    assert len(calls) == 1 + web.MAX_PAGES == 3
    assert calls[0][1] == 8 and all(c[1] == 6 for c in calls[1:])
    assert parse_qs(urlsplit(calls[0][0]).query) == {
        'q': [QUERY], 'format': ['json'], 'language': ['ko-KR'], 'safesearch': ['0']}
    assert pages[0].read_sizes == [PAGE_BYTES]
    assert result.results[0].page.startswith('본문 & 내용 다음 문장')
    assert '숨길' not in result.results[0].page and len(result.results[0].page) == web.PAGE_CHARS
    assert result.results[0].engines == ('one', 'two') and result.unresponsive_engines
    block = render_results(result)
    assert '요약 1' in block and '요약 2' in block and 'URL: https://site0.example/page' in block
    assert len(block) <= web.BLOCK_CHARS


def test_plain_text_charset_and_whitespace(monkeypatch):
    response = HttpResponse(' 문장\n 두 줄\t 끝 '.encode('euc-kr'), 'text/plain; charset=euc-kr')
    monkeypatch.setattr('companion.adapters.web_search.urlopen', lambda *a, **kw: response)
    assert SearXNGWebSearch._page('https://example.org') == '문장 두 줄 끝'


@pytest.mark.parametrize('payload', [b'broken', b'[]', b'{}', b'{"results":true}',
                                    b'{"results":[{}]}'])
def test_adapter_bad_json_becomes_search_error(monkeypatch, payload):
    monkeypatch.setattr('companion.adapters.web_search.urlopen',
                        lambda *a, **kw: HttpResponse(payload))
    with pytest.raises(WebSearchError):
        SearXNGWebSearch().search('test')


@pytest.mark.parametrize('error', [URLError('absent'), TimeoutError('timeout')])
def test_adapter_transport_failure(monkeypatch, error):
    def http(*a, **kw):
        raise error
    monkeypatch.setattr('companion.adapters.web_search.urlopen', http)
    with pytest.raises(WebSearchError):
        SearXNGWebSearch().search('test')


def test_render_total_limit_and_site_urls():
    result = WebSearchResult(QUERY, tuple(WebSearchItem(
        '긴 제목'*100, f'https://site{i}.example', '발췌'*3000) for i in range(5)))
    block = render_results(result)
    assert len(block) <= web.BLOCK_CHARS and block.startswith('검색 결과 (검색어: '+QUERY+', 5건)')
    assert '[1]' in block and '— site0.example' in block and 'URL: https://site0.example' in block


def test_fixed_prefix_unchanged_between_queries(h):
    first = h.runtime.core._prepare('날씨 검색해봐').request
    second = h.runtime.core._prepare('한양이엔지 알아봐').request
    first_index = next(i for i, m in enumerate(first.messages) if WEB_INSTRUCTION in m.content)
    second_index = next(i for i, m in enumerate(second.messages) if WEB_INSTRUCTION in m.content)
    assert first_index == second_index
    assert first.messages[:first_index + 1] == second.messages[:second_index + 1]


def test_no_stream_model_searches_and_stores_final(h):
    from test_schedule_dialogue import Model

    model = Model()
    model.queue.extend([proposed(), proposed(None, FINAL)])
    h.runtime.core._chat_model = model
    response = h.runtime.core.respond_to_text('날씨 검색해봐')
    assert response.text == FINAL and len(model.requests) == 2
    assert h.runtime.resources.repository.list_messages()[-1].content == FINAL


def test_every_turn_can_search_without_session_quota(h):
    for _ in range(3):
        h.model.queue.extend([proposed(), proposed(None, FINAL)])
        assert h.runtime.core.respond_to_text('날씨 검색해봐').text == FINAL
    assert len(h.dialogue.web_search.queries) == 3 and h.model.stream_calls == 6
    assert all(record['calls'] == 2 for record in h.observations())


def test_search_keeps_only_first_codex_request(h):
    from companion.worker import ReadOnlyWorkerRequest

    first = json.loads(proposed())
    first['codex_request'] = dict(task='README 확인', why='실제 코드가 필요해.')
    second = json.loads(proposed(None, FINAL))
    second['codex_request'] = dict(task='다른 파일 확인', why='두 번째 요청')
    h.model.queue.extend([json.dumps(first), json.dumps(second)])
    response = h.runtime.core.respond_to_text('README도 확인하고 날씨 검색해봐')
    assert response.worker_request == ReadOnlyWorkerRequest(**first['codex_request'])
    assert response.text == FINAL and h.model.stream_calls == 2


def test_empty_search_and_non_http_urls_never_fetch_pages(monkeypatch):
    calls = []

    def http(url, **kwargs):
        calls.append(url)
        return HttpResponse(json.dumps(dict(results=[
            dict(row(0), url='file:///etc/passwd'), dict(row(1), url='ftp://site.example'),
        ])).encode())

    monkeypatch.setattr('companion.adapters.web_search.urlopen', http)
    result = SearXNGWebSearch().search('검색')
    assert not result.results and result.pages == 0 and len(calls) == 1


def test_adapter_oversized_search_response_rejected(monkeypatch):
    from companion.adapters.web_search import SEARCH_BYTES

    response = HttpResponse(b' ' * (SEARCH_BYTES + 1))
    monkeypatch.setattr('companion.adapters.web_search.urlopen', lambda *a, **kw: response)
    with pytest.raises(WebSearchError):
        SearXNGWebSearch().search('검색')
    assert response.read_sizes == [SEARCH_BYTES + 1]


def test_page_disconnect_is_snippet_fallback(monkeypatch):
    from http.client import IncompleteRead

    def http(request, **kwargs):
        if isinstance(request, str):
            return HttpResponse(json.dumps(dict(results=[row(0)])).encode())
        raise IncompleteRead(b'partial')

    monkeypatch.setattr('companion.adapters.web_search.urlopen', http)
    result = SearXNGWebSearch().search('검색')
    assert result.pages == 0 and '요약 0' in render_results(result)


def test_web_followup_does_not_bypass_existing_blocking_claim_gate(h):
    h.model.queue.extend([proposed(), proposed(None, '파일을 바로 수정할게.')])
    response = h.runtime.core.respond_to_text('검색해봐')
    assert response.text != '파일을 바로 수정할게.' and h.model.stream_calls == 2
