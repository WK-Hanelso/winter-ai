"""Measured web budgets, fake server properties and no live network calls."""

from dataclasses import replace
import json
from urllib.error import URLError

from configs.dialogue import context, web
import pytest
from test_llama_cpp_adapter import FakeHttpResponse
from test_p8_web import FINAL, QUERY, Search, proposed
from test_schedule_dialogue import Harness

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.application.bootstrap import build_text_runtime
from companion.web_search import WebSearchItem, WebSearchResult, render_results

RESULT = WebSearchResult(QUERY, tuple(
    WebSearchItem(f'제목 {i}', f'https://site{i}.example/page', f'요약 {i} ' + '가' * 150,
                  page='본문' * 400 if i < 2 else '') for i in range(5)))


@pytest.mark.parametrize(('ctx', 'prompt', 'max_tokens', 'expected'), [
    (8192, 6000, 896, 1664),
    (8192, 6000, 768, 1868),
    (16384, 6000, 768, 2200),
    (8192, None, 768, 2200),
    (8192, 8000, 768, 0),
])
def test_budget_and_snippets_before_pages(ctx, prompt, max_tokens, expected):
    block = render_results(RESULT, context_tokens=ctx, prompt_tokens=prompt, max_tokens=max_tokens)
    assert len(block) <= expected
    if expected == 0:
        assert block == ''
        return
    assert len(block) == min(expected, len(render_results(RESULT)))
    for item in RESULT.results:
        assert item.content in block and 'URL: ' + item.url in block
    if prompt is None:
        assert block == render_results(RESULT)


def test_oversize_snippets_trim_only_after_removing_pages():
    result = replace(RESULT, results=tuple(replace(i, content=i.content + '가' * 700)
                                           for i in RESULT.results))
    block = render_results(result, context_tokens=8192, prompt_tokens=6000, max_tokens=896)
    assert len(block) <= 1664 and '본문' not in block
    assert all(f'요약 {i}' in block and item.url in block
               for i, item in enumerate(result.results))


@pytest.mark.parametrize('budget', [0, 1, 30, 100, 200, 700, 1664, 2200])
def test_tiny_budgets_never_split_url_or_overflow(monkeypatch, budget):
    monkeypatch.setattr(web, 'BLOCK_CHARS', budget)
    block = render_results(RESULT)
    assert len(block) <= budget
    for line in block.splitlines():
        if line.startswith('URL: '):
            assert line[5:] in [item.url for item in RESULT.results]
    if block:
        assert 'URL: ' in block


@pytest.mark.parametrize('props,expected', [
    ({'n_ctx': 8192}, 8192),
    ({'default_generation_settings': {'n_ctx': 8192}, 'n_ctx': 32768}, 8192),
    ({'default_generation_settings': {'n_ctx': 16384}}, 16384),
    ({}, 16384), ([], 16384), ({'n_ctx': True}, 16384),
    ({'n_ctx': 0}, 16384), ({'n_ctx': '8192'}, 16384), ({'n_ctx': -1}, 16384),
])
def test_props_read_and_fallback(monkeypatch, props, expected):
    calls = []

    def http(request, timeout):
        calls.append((request.full_url, request.method, timeout))
        return FakeHttpResponse(json.dumps(props).encode())

    monkeypatch.setattr('companion.adapters.llama_cpp.urlopen', http)
    assert LlamaCppHttpChatModel('http://fake/').read_context_tokens() == expected
    assert calls == [('http://fake/props', 'GET', 2.0)]


@pytest.mark.parametrize('failure', [URLError('absent'), TimeoutError(), b'broken JSON'])
def test_props_failure_uses_config_default(monkeypatch, failure):
    def http(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return FakeHttpResponse(failure)

    monkeypatch.setattr('companion.adapters.llama_cpp.urlopen', http)
    assert LlamaCppHttpChatModel('http://fake').read_context_tokens() == context.CONTEXT_TOKENS


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('ctx,prompt', [(8192, 6000), (16384, 6000), (8192, None), (8192, 8000)])
def test_startup_props_once_and_first_call_timings_control_followup(
    tmp_path, monkeypatch, streaming, ctx, prompt,
):
    calls = []
    replies = iter([proposed(), proposed(None, FINAL)])

    def http(request, timeout):
        calls.append(request.full_url)
        if request.full_url.endswith('/props'):
            return FakeHttpResponse(json.dumps({'default_generation_settings':
                                                {'n_ctx': ctx}}).encode())
        text = next(replies)
        # Deliberately different second-call timing must not replace first-call data.
        timings = {'prompt_n': prompt if len(calls) == 2 else 12}
        if streaming:
            chunks = [dict(choices=[dict(delta=dict(content=text), finish_reason='stop')])]
            if prompt is not None:
                chunks.append(dict(choices=[], timings=timings))
            return FakeHttpResponse((''.join('data: ' + json.dumps(c) + '\n' for c in chunks)
                                     + 'data: [DONE]\n').encode())
        body = dict(choices=[dict(message=dict(content=text), finish_reason='stop')])
        if prompt is not None:
            body['timings'] = timings
        return FakeHttpResponse(json.dumps(body).encode())

    monkeypatch.setattr('companion.adapters.llama_cpp.urlopen', http)
    h = Harness(tmp_path)
    h.runtime.close()
    adapter = LlamaCppHttpChatModel('http://fake')
    h.runtime = build_text_runtime(h.config, chat_model=adapter)
    h.dialogue = h.runtime.core._schedule_dialogue
    h.dialogue.web_search = Search(RESULT)
    requests = []

    class Model:
        def generate(self, request):
            requests.append(request)
            return adapter.generate(request)

    class StreamModel(Model):
        def generate_stream(self, request):
            requests.append(request)
            return (yield from adapter.generate_stream(request))

    h.runtime.core._chat_model = StreamModel() if streaming else Model()
    try:
        assert calls == ['http://fake/props'] and h.dialogue.context_tokens == ctx
        response = h.runtime.core.respond_to_text('날씨 검색해봐')
        assert calls.count('http://fake/props') == 1
        if prompt == 8000:
            assert len(requests) == 1 and '공간이 부족' in response.text
            assert not h.observations()[-1]['web_grounded']
        else:
            assert len(requests) == 2 and response.text == FINAL
            first, second = requests
            assert second.messages[:len(first.messages)] == first.messages
            assert second.messages[-1].content == render_results(
                RESULT, context_tokens=ctx, prompt_tokens=prompt, max_tokens=first.max_tokens)
        assert h.observations()[-1]['prompt_tokens'] == prompt
    finally:
        h.runtime.close()


def test_fake_no_timings_uses_cap_without_props(tmp_path):
    h = Harness(tmp_path)
    h.dialogue.web_search = Search(RESULT)
    h.model.queue.extend([proposed(), proposed(None, FINAL)])
    try:
        h.runtime.core.respond_to_text('검색해봐')
        assert h.model.requests[-1].messages[-1].content == render_results(RESULT)
        assert h.observations()[-1]['prompt_tokens'] is None
    finally:
        h.runtime.close()
